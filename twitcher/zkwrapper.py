#!/usr/bin/python26

"""Wrapper for zookeeper that manages watches.

This is a simple wrapper for zookeeper that allows us to use a single watch
to handle many watchers. This should vastly reduce zookeeper overhead when
many watches on the same thing are used.

This class also allows us to unregister watches which is something that
zookeeper doesn't do normally. This is important because the lack of unregister
means that an object can not be garbage collected (by nature of its reference
in the zookeeper module) until its watch fires. To handle this we instead
use this class to receive the watches with the knowledge that it shouldn't
be created and destroyed often.

Author: Brady Catherman (brady@twitter.com)
"""

import logging
import os
import socket
import threading
import zookeeper


class ZKWrapper(object):
  """Wraps all zookeeper functionality into a simple wrapper.

  This is a basic wrapper for a zookeeper handle. The idea is to allow
  multiplexing of zookeeper requests so we can reduce the watch counts. This
  also allows us to unregister watches which is something the normal zookeeper
  library doesn't support.

  Args:
    servers: the list of zookeeper servers to connect too.
  """
  def __init__(self, servers):
    logging.debug('Creating ZKwrapper against %s', ','.join(servers))
    self._servers = []
    for s in servers:
      parts = s.split(':')
      if len(parts) == 2:
        self._servers.append((parts[0], parts[1]))
      else:
        self._servers.append((s, 2181))
    self._lock = threading.Lock()
    self._watcher_lock = threading.Lock()
    self._watches = {}
    self._handlers = {}
    self._zookeeper = None
    self._clientid = None
    self._pending_gets = []
    self._connect()

  def _global_watch(self, zh, event, state, path):
    """Called when the connection to zookeeper has a state change."""
    if state == zookeeper.EXPIRED_SESSION_STATE:
      self._connect()
    if state == zookeeper.CONNECTED_STATE:
      self._clientid = zookeeper.client_id(self._zookeeper)
      # Catch up all gets requested before we were able to connect.
      while self._pending_gets:
        path, w, h = self._pending_gets.pop()
        zookeeper.aget(self._zookeeper, path, w, h)

  def _connect(self):
    """Creates a connection to a zookeeper instance."""
    s = []
    for host, port in self._servers:
      try:
        _, _, ips = socket.gethostbyname_ex(host)
        for ip in ips:
          s.append('%s:%s' % (ip, port))
      except socket.gaierror:
        logging.error('Hostname not known: %s', host)
      except socket.herror:
        logging.error('Unable to resolve %s', host)

    if not s:
      logging.error('No IPs found to connect to.. trying again in 1 second.')
      t = threading.Timer(1.0, self._connect)
      t.daemon = True
      t.start()
      return

    if self._clientid is not None:
      # Existing connections get registered with the same clientid that was
      # used before.
      self._zookeeper = zookeeper.init(
          ','.join(s), self._global_watch, None, clientid)
    else:
      self._zookeeper = zookeeper.init(','.join(s), self._global_watch)

  def aget(self, path, watcher=None, handler=None):
    """A simple wrapper for zookeeper async get function.

    This function wraps the zookeeper aget call which allows the caller
    to register a function to handle the data once it is received as well
    as a function that will be called once the data has been updated. If
    neither is given then this function will do nothing.

    Args:
      path: The znode to watch.
      watcher: Called when the given znode is updated or changed. the basic
               footprint of this function is:
                 func(zh, path)
                 zh will be this object, and path will be the znode path.
      handler: Called when the data has been fetched from zookeper. The basic
               footprint of this function is:
                 func(zh, path, rc, data)
                 zh will be this object and path will be the znode path.
                 rc is the return code from zookeeper.
                 data is the contents of the znode.

    Returns:
      Nothing.
    """
    register = False
    get = False
    self._lock.acquire()
    if watcher:
      register = path not in self._watches
      self._watches.setdefault(path, []).append(watcher)
    if handler:
      get = path not in self._handlers
      self._handlers.setdefault(path, []).append(handler)
    self._lock.release()
    if register or get:
      if register:
        w = self._watcher
      else:
        w = None
      # We use a lambda here so we can make sure that the path gets appended
      # to the args. This allows us to multiplex the call.
      h = (lambda zh, rc, data, stat: self._handler(zh, rc, data, stat, path))
      logging.debug('Performing a get against %s', path)
      zookeeper.aget(self._zookeeper, path, w, h)

  def unregister(self, path, watcher=None, handler=None):
    """Removes an existing watch or handler.

    This unregisters an object's watch and handler callback functions. It
    doesn't actually prevent the watch or hanler from triggering but it
    does remove all references frmo the object and prevent the functions
    from being called. This allows garbage collection of the object.

    Args:
      path: The znode being watched.
      watcher: The watcher function that should be removed.
      handler: The handler function that should be removed.

    Returns:
      Nothing.
    """
    if watcher:
      try:
        while True:
          self._watches.get(path, []).remove(watcher)
      except ValueError:
        pass
    if handler:
      try:
        while True:
          self._watches.get(path, []).remove(handler)
      except ValueError:
        pass

  def _watcher(self, zh, event, state, path):
    """Internal function called by zookeeper when a node updates.

    This function is called by zookeeper when any of the watched nodes update.
    We use this is a simple wrapper so that the zookeeper module doesn't
    actually have to have a reference to any object other than this one. This
    is important if you want to actually support unregistering of watches
    otherwise the unregistered object will retain a reference in the
    zookeeper module which prevents gc.

    Args:
      zh: The real zookeeper handler object that created the watch.
      event: The event that triggered this watch.
      state: The state of the connection.
      path: The znode that triggered this watch.

    Returns:
      Nothing.
    """
    if event == zookeeper.SESSION_EVENT:
      return
    logging.info('Received a zookeeper watcher notification for %s', path)
    watches = self._watches.pop(path, None)
    # We lock this while we call all the registered watchers so they all
    # have a chance to call aget() in order to get the data _before_ we
    # process the returned data. This allows for better batching of get
    # requests so we can reduce load on the zookeeper servers.
    self._watcher_lock.acquire()
    while watches:
      callback = watches.pop()
      callback(self, path)
    self._watcher_lock.release()

  def _handler(self, zh, rc, data, stat, path):
    """Handles zookeeper data calls.

    This function is called once an aget() request completes. It returns
    the data in the znode back to the caller. In the _watcher function
    above we use a lambda as the real call back in order allow passing of
    the znode in which it seems the zookeeper library doesn't do.

    Args:
      zh: the zookeeper object the watched was registered against.
      rc: The return code from the call.
      data: The contents of the znode.
      stat: The stat data from the call.
      path: The znode that we are getting the contents of.

    Returns:
      Nothing.
    """
    if rc == zookeeper.OK:
      logging.info('Received znode contents for %s', path)
      logging.debug('Contents of %s\n"""%s""".', path, data)
      # This lock means that we will not process the handler until all
      # watchers have been notified.
      self._watcher_lock.acquire()
      handlers = self._handlers.pop(path, None)
      self._watcher_lock.release()
      while handlers:
        handler = handlers.pop()
        handler(self, rc, data, path)
    elif rc == zookeeper.CONNECTIONLOSS:
      logging.info('Watch event triggered for %s: Connection loss.', path)
      h = (lambda zh, rc, data, stat: self._handler(zh, rc, data, stat, path))
      self._pending_gets.append((path, self._watcher, h))

#!/usr/bin/python26

"""Core functionality for Twitcher.

This module contains the core functionality for twtcher. The main object
here is the TwitcherObject which acts as a simple wrapper for each watch.

Author: Brady Catherman (brady@twitter.com)
"""

import grp
import logging
import os
import resource
import threading
import types
import pwd
import sys
import time
import zookeeper

# Twitcher object
import zkwrapper


# The default ZKWrapper object to use when registering watches.
default_zkwrapper = None

# The default file descriptor to send '\0' to if we span a new process.
# This is used to allow select to restart which will pick up the new
# stdin that needs written too.
default_ping_fd = None

def set_default_zkwrapper(obj):
  """Sets the value of default_zkwrapper."""
  global default_zkwrapper
  default_zkwrapper = obj


def set_default_ping_fd(fd):
  """Sets the value of default_ping_fd."""
  global default_ping_fd
  default_ping_fd = fd


QUEUE = 1
PARALLEL = 2
DISCARD = 3


class UnknownUserError(Exception):
  pass


class UnknownGroupError(Exception):
  pass


class MinimalSubprocess(object):
  """Wraps much of the functionality from subprocess.

  This is a basic wrapper of subprocess except that it allows us to not
  exec. This makes twitcher very unix specific as it uses several unix only
  calls.

  The basic idea here is that we fork and then run a python function that can
  be anything from an exec to a custom block of code from the config file.

  We fork in order to protect the main twitcher process and so all basic
  actions look exactly the same.

  Args:
    desc: The string description of this subprocess.
    data: The data we should write to stdin of the forked process.
  """
  def __init__(self, desc, data):
    logging.debug('Creating MinimalSubprocess (%s): %s', self, desc)
    self.stdin = None
    self.pid = -1
    self.desc = desc
    self.data = data

  def __del__(self):
    """Verifies that the file descripts all get closed properly."""
    logging.debug('Reaping MinimalSubprocess "%s" (%s)', self.desc, self)
    if self.stdin is not None:
      try:
        os.close(self.stdin)
      except OSError:
        pass

  def poll(self):
    """Tests to see if the process has exited.

    This function mimics subprocess.Popen.poll(). It returns None if the
    process has not exited yet and if it has it will set returncode and
    return the vaule.

    Once this call has returned a value other than None further calls will
    result in a OSError being thrown.

    Returns:
      None or a return code.
    """
    logging.debug('Polling for process "%s" (%s)', self.desc, self.pid)
    r = os.waitpid(self.pid, os.WNOHANG)
    if r == (0, 0):
      return None
    self.returncode = r[1]
    return r[1]

  def write_buffer(self):
    """Attempts to write data to stdin.

    This will attempt to write the data passed into the constructor to
    stdin. If the data is written completely then stdin will be closed,
    otherwise it will retain the remainder of data that has not been written
    for further calls.

    Throws:
      OSError: If called after a non None value is returned.

    Returns:
      Nothing.
    """
    if self.stdin is not None:
      written = os.write(self.stdin, self.data)
      self.data = self.data[written:]
      if not self.data:
        self.data = None
        os.close(self.stdin)
        self.stdin = None

  def _child_exec(self, stdin_fd, func, uid=None, gid=None):
    """Called to setup the child after the fork.

    This function handles all client operations post fork. The main
    functionality presented here is to close all file descriptors other than
    stdin/stdout/stderr, and to configure those three sockets to have a pipe
    back to the parent.

    Args:
      stdin_fd: The file descriptor created via pipe() that should become
                our stdin.
      func: The function we should run once setup properly.
      uid: The userid (int) to switch to after forking.
      gid: The groupid (int) to switch to after forking.

    Throws:
      OSError: Any error during the dup/close cycle.

    Returns:
      Nothing.
    """
    os.dup2(stdin_fd, 0)
    f = os.open('/dev/null', os.O_WRONLY)
    os.dup2(f, 1)
    os.dup2(f, 2)

    # Close all open file descriptors besides stdin/stdout/stderr
    maxfd = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    if (maxfd == resource.RLIM_INFINITY):
      maxfd = MAXFD
    for fd in xrange(3, maxfd):
      try:
        os.close(fd)
      except OSError:
        pass

    # Switch the userid if needed.
    if gid:
      os.setgid(gid)
    if uid:
      os.setuid(uid)

    # Run our function.
    func()

  def fork_exec(self, func, uid=None, gid=None):
    """Calls fork and mimics exec.

    This function forks and mimics the exec call by calling the function passed
    into the constructor for this object. This function must be called before
    all other functions in this object are called.

    Args:
      func: The function that should be called post fork.
      uid: The userid (int or string) to switch to after forking.
      gid: The groupid (int or string) to switch to after forking.

    Throws:
      OSError: Any kind of error during the fork cycle.

    Returns:
      Nothing.
    """
    try:
      # Convert string based user names and group names into integers.
      if type(uid) == types.StringType:
        ud = pwd.getpwnam(uid)
        if not ud:
          raise UnknownUserError()
        uid = ud.pw_uid
      if type(gid) == types.StringType:
        groups = pwd.getpwall()
        gd = grp.getgrnam(gid)
        if not gd:
          raise UnknownGroupError()
        gid = gd.gr_gid

      stdin = os.pipe()
      pid = os.fork()
      if pid < 0:
        raise OSError('Unknown problem with fork()')
      elif pid == 0:
        # Child.
        r = self._child_exec(stdin[0], func, uid=uid, gid=gid)
        if type(r) == int:
          os._exit(r)
        elif r is None:
          os._exit(0)
        os._exit(1)
      else:
        # Parent
        self.pid = pid
        self.stdin = stdin[1]
        os.close(stdin[0])
    except OSError, e:
      logging.error('OSError while forking for "%s": %s', self.desc, e)
      raise


class TwitcherObject(object):
  """Manages a single Twitcher configuration

  This function manages a single Twitcher configuration. There may be
  more than one configuration per file so this is not one to one with
  files. Instead this is a basic node: script pairing.

  This manages the zookeeper watch and when triggered will execute the
  intended action and watch that cycle. Once finished it will reregister
  watches and continue working.

  Args:
    run_func: The function that should be run in the subprocess once a watch
              or initial load (see run_on_load) has initiated the process.
    pipe_stdin: Retrieve the contents of the node and pipe them to stdin
                on the subprocess. If False then nothing will be piped to
                stdin.
    run_on_load: Should this script be run when this config is loaded.
                 If this is True (default) then the script will be started
                 when twitcher loads and when watches fire.
    run_mode: This defines how Twitcher should react when a watch is updated
              while the script is currently running. This should be one of
              the following:
              core.QUEUE: Queue then run once the current running script
                          finishes. Note that this will run one instance after
                          the current script finishes regardless of how many
                          times the watch may have fired in the mean time.
              core.PARALLEL: Run scripts in parallel.
              core.DISCARD: Discard requests received while the script is
                            currently running.
    uid: The user id the process should run as.
    gid: The group id the process should run as.
    description: The basic description of this command (ex: command line)
  """
  def __init__(self, path, run_func,
               pipe_stdin=True, run_on_load=True,
               run_mode=QUEUE, uid=None, gid=None,
               description='generic object'):
    self._path = path
    self._run_func = run_func
    self._pipe_stdin = pipe_stdin
    self._run_on_load = run_on_load
    self._run_mode = run_mode
    self._processes = []
    self._description = description
    self._uid = uid
    self._gid = gid
    self._unhandled_watch = None
    self._lock = threading.Lock()

  def init(self):
    """Called to initialize this object.

    This is split out from the __init__ call to allow the msot likely error
    conditions to be captured be twitcher rather than the configuration
    object.

    Returns:
      Nothing.
    """
    logging.debug('Initializing %s', self._description)
    self._register_watch(handler=self._run_on_load)

  def get_fds(self):
    """Returns a list of all file descriptors of subprocesses.

    This is used for select() calls. It should return a list of all child
    process file descriptors as a tuple ([r1, r2], [w1, w1]).

    Returns:
      A 2 element tuple containing a list of read file descriptors, and
      write file descriptors.
    """
    r_fds = []
    w_fds = []
    self._lock.acquire()
    for p in self._processes:
      if p.stdin is not None:
        w_fds.append(p.stdin)
    self._lock.release()
    return (r_fds, w_fds)

  def sigchld(self):
    """Called when SIGCHLD is received.

    This should call poll on all subprocesses in order to find out if they
    have exited. This is used to clear out child processes that have completed.

    This function shouldn't block.

    Returns:
      Nothing.
    """
    removed = []
    self._lock.acquire()
    for p in self._processes:
      r = p.poll()
      if r is not None:
        logging.warning('Process "%s" (%s) exited with code %s',
                        p.desc, p.pid, r)
        self._processes.remove(p)
        removed.append(p)
    self._lock.release()
    if removed:
      self._post_exec()

  def select(self, r, w):
    """Called when a socket is read/writable.

    This should check to see if any of the sockets listed in r and w are
    attached to any subprocess so we can read/write to them.

    This function shouldn't block.

    Args:
      r: A list of file descriptors that can be read from.
      w: A list of file descriptors that can be written too.

    Returns:
      Nothing.
    """
    self._lock.acquire()
    for p in self._processes:
      if p.stdin in w:
        p.write_buffer()
    self._lock.release()

  def _register_watch(self, handler=True):
    """Called to actually register a watch (and perform a get if needed.)

    This function will wrap the zookeeper calls in order to make it easy to
    register a watch and fetch data regardless of the options we have
    been configured with.

    Args:
      handler: Optional. If true (default) then self._handler will be called
               when new data is received, otherwise nothing will be called.

    Returns:
      Nothing.
    """
    if handler is True:
      h = self._handler
    else:
      h = None
    default_zkwrapper.aget(self._path, handler=h, watcher=self._watch)

  def _exec(self, data):
    """Starts the registered fuction as a second process

    This will fork and start the registered function on a second process.
    This shouldn't block on anything. It mearly starts then returns.

    Args:
      data: The data that should be written to stdin on the sub process.

    Returns:
      Nothing.
    """
    logging.warning('Executing process: %s' % self._description)
    try:
      p = MinimalSubprocess(self._description, data)
      p.fork_exec(self._run_func, self._uid, self._gid)
      self._lock.acquire()
      self._processes.append(p)
      self._lock.release()
      if default_ping_fd is not None:
        os.write(default_ping_fd, '\0')
    except UnknownUserError:
      logging.error('%s: Unable to find user %s', self._description,
                    self._uid)
    except UnknownGroupError:
      logging.error('%s: Unable to find group %s', self._description,
                    self._gid)

  def _post_exec(self):
    """Run once the script has finished executing.

    This will clean up after a script run. It should reexecute the script if
    QUEUE mode is selected and we received a watch update while running the
    script. If needed it will also reregister the watch so that we continue
    getting update notifications.

    This function may call _watch() but shouldn't be expected to block.

    Returns:
      Nothing.
    """
    if self._unhandled_watch:
      if self._run_mode == DISCARD:
        # If we are in discard mode we simply discard the data we will
        # get back from zookeeper.
        self._register_watch(handler=False)
      elif self._run_mode == QUEUE:
        # Re run the watch that we missed as though we just received it. We do
        # this by passing the arguments back into the mix.
        logging.debug('Processing queued watches on %s',
                      self._unhandled_watch[1])
        args = self._unhandled_watch
        self._unhandled_watch = None
        self._watch(*args)
      else:
        # We shouldn't ever get here.
        # FIXME(brady):
        logging.error('_run_mode is invalid in %s' % self)

  def _watch(self, zh, path):
    """Called when a zookeeper node we are watching updates.

    This function is called by zookeeper when a node we have registered a watch
    on has changed in some way. Depending on the configuration this function
    may call _exec() to start the subprocess or may perform an async get on
    zookeeper in order to get the contents of the node. Either way it shouldn't
    block.

    Args:
      zh: The zookeeper handle that created the watch.
      event: The event that triggered the watch.
      state: The connection state.
      path: The znode that triggered this watch.

    Returns:
      Nothing.
    """
    logging.info('Received watch notification for %s', path)
    if self._processes and self._run_mode != PARALLEL:
      logging.warning('Postponing processing of "%s" '
                      '(a scipt is already running).', self._description)
      self._unhandled_watch = (zh, path)
      return
    self._register_watch()
    if not self._pipe_stdin:
      # We don't need to wait for the data to arrive to execute in this mode
      self._exec('')

  def _handler(self, zh, rc, data, path):
    """Called with the data after an aget() request.

    This function is called by the ZKWrapper object once the data for a
    znode has been fetched. In all cases we should exec the script unless
    something has gone wrong.

    Args:
      zh: The ZKWrapper object that is calling us.
      rc: The return code from zookeeper.
      data: The contents of the znode.
      path: The znode that updated.

    Returns:
      Nothing.
    """
    if rc == zookeeper.OK:
      self._exec(data)
    else:
      # Failure!
      # FIXME(brady)
      return

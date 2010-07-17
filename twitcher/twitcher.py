#!/usr/bin/python26

"""Main loop for the twitcher binary.

This class contains the main "Twitcher" object which is a simple wrapper for
the main loop functionality. It catches SIGCHLD, and manages the select loop.
It also creates the top level zookeeper and config directory watcher objects.

Author: Brady Catherman (brady@twitter.com)
"""

import errno
import logging
import os
import select
import signal

# Twitcher modules
from inotify import InotifyWatcher
from config import ConfigFile
import core
import zkwrapper


class Twitcher(object):
  """The main operating loop of the twitcher program.

  Args:
    zkservers: A common seperated list of zookeeper servers to connect too.
    config_path: The path to (recursively) read config files from.
  """
  def __init__(self, zkservers, config_path):
    self._signal_notifier = os.pipe()
    signal.set_wakeup_fd(self._signal_notifier[1])
    signal.signal(signal.SIGCHLD, self._sigchld)
    zh = zkwrapper.ZKWrapper(zkservers)
    core.set_default_zkwrapper(zh)
    core.set_default_ping_fd(self._signal_notifier[1])
    self._inotify_watcher = InotifyWatcher([config_path], ConfigFile,
                                           self._is_config_file)
    self._sigchld_received = False

  def _is_config_file(self, filename):
    """Returns True if the file name is a twitcher config file."""
    return filename.endswith('.twc')

  def _sigchld(self, sig, frame):
    """Called when a SIGCHLD signal has been received."""
    signal.signal(signal.SIGCHLD, self._sigchld)
    self._sigchld_received = True

  def _get_all_config_objects(self):
    """Returns a list of all config objects loaded."""
    watch_files = self._inotify_watcher.files()
    r = []
    for w in watch_files:
      r += w.get_configurations()
    return r

  def run(self):
    """The main running loop of the twitcher process. Doesn't return."""
    while True:
      # If we received SIGCHLD then we should allow our config modules
      # to reap all children.
      if self._sigchld_received:
        for c in self._get_all_config_objects():
          c.sigchld()
        self._sigchld_received = False

      # We add our notified file descriptor by default so select will exit
      # when sigchld is received.
      r_fds = [self._signal_notifier[0]]
      w_fds = []
      for c in self._get_all_config_objects():
        cr, cw = c.get_fds()
        r_fds += cr
        w_fds += cw

      try:
        iready, oready, e = select.select(r_fds, w_fds, [], 60)
        if not iready and not oready and not e:
          # On timeouts we check all child processes for safety. This makes
          # sure we don't lose one somehow.
          _sigchld_received = True
          logging.debug('select loop timed out without updates.')
        else:
          if self._signal_notifier[0] in iready:
            os.read(self._signal_notifier[0], 1)
          for c in self._get_all_config_objects():
            c.select(iready, oready)
      except select.error, v:
        if v[0] != errno.EINTR:
          raise

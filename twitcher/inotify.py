#!/usr/bin/python26

"""Watches a list of directories for file updates.

The classes in this module  will watch a list of subdirectories for file
updates. A class is passed in at object initialization time and is used to
create objects as new files are discovered. If a file is updated then the
reload() function on that class will be called. If the file is removed the
class will be deleted.

It is important to verify that __init__, __del__, and reload() are all
defined properly.

A simple example of this module use looks like this:
    class watcher(object):
      def __init__(self, filename):
        self._filename = filename
        print 'Init: %s' % filename
      def __del__(self):
        print 'Del: %s' % self._filename
      def reload(self):
        print 'reload: %s' % self._filename

    x = inotify.InotifyWatcher(['/tmp/bar'], watcher)

Only one InotifyWatcher can be registered per process due to the way that
inotify works.

Author: Brady Catherman (brady@twitter.com)
"""

import fcntl
import logging
import os
import signal
import stat


WATCH_MASK = (fcntl.DN_MODIFY | fcntl.DN_CREATE | fcntl.DN_DELETE |
              fcntl.DN_RENAME | fcntl.DN_MULTISHOT)


class WatchClass(object):
  """Interface class to be passed into InotifyWatcher()"""
  def __init__(self, filename):
    pass

  def __del__(self):
    pass

  def reload(self):
    """Called when the file is updated on disk."""
    pass


class InotifyWatcher(object):
  """Watches a list of directories for updates to the files in them.

  This class will watch the directories in watch_directories and will
  automatically make a class of watch_class type when a new one is found.

  Args:
    watch_directories: An iterable list of directories to watch for files in.
    watch_class: The class that will be used to wrap each file.
    file_pattern: An optional function that filters filenames. The basic
                  footprint takes a single parameter (the filename) and returns
                  True/False if it should be watched or not. If this is not
                  given then all files will be watched.
  """
  def __init__(self, watch_directories, watch_class, file_pattern=None):
    if file_pattern is None:
      file_pattern = (lambda x: True)
    self._watch_directories = watch_directories
    self._watch_class = watch_class
    self._file_pattern = file_pattern
    self._watch_fds = {}
    self._watch_files = {}
    signal.signal(signal.SIGIO, self._inotify)
    signal.signal(signal.SIGHUP, self._inotify)
    self.rescan()

  def _recurse_directory(self):
    """Recurses through all self._watch_directories finding files."""
    all_files = set()
    dirs = set(self._watch_directories)
    all_dirs = set()
    while dirs:
      dir = dirs.pop()
      try:
        files = [os.path.join(dir, f) for f in os.listdir(dir)]
        all_dirs.add(dir)
        all_files.update([f for f in files
                          if os.path.isfile(f) and self._file_pattern(f)])
        dirs.update([f for f in files if os.path.isdir(f) and f[0] != '.'])
      except IOError, e:
        logging.warning('Unable to access: %s' % dir)
      except OSError, e:
        logging.warning('Unable to access: %s' % dir)
    return (all_dirs, all_files)

  def _register_inotify(self, dir):
    """Registers a watch on the given directory."""
    if dir in self._watch_fds:
      return
    logging.info('Registering a inotify watch on %s' % dir)
    try:
      fd = os.open(dir, os.O_RDONLY)
      fcntl.fcntl(fd, fcntl.F_NOTIFY, WATCH_MASK)
      self._watch_fds[dir] = fd
    except IOError, e:
      logging.error('Unable to register watch on %s: %s' % (dir, e))

  def _unregister_inotify(self, dir):
    """Unregisters the directory for update notification."""
    if dir not in self._watch_fds:
      return
    logging.info('Unregistering a inotify watch on %s' % dir)
    del self._watch_fds[dir]

  def _inotify(self, signum, frame):
    """Called when either SIGHUP or SIGIO (inotify) is received."""
    logging.info('Received SIGHUP or a file update notification.')
    signal.signal(signal.SIGIO, self._inotify)
    signal.signal(signal.SIGHUP, self._inotify)
    self.rescan()

  def _mtime(self, filename):
    """Returns the mtime of the given file (in seconds)."""
    try:
      s = os.stat(filename)
      return s[stat.ST_MTIME]
    except IOError:
      # On error we just return zero..
      # FIXME[brady]: Make this work better.
      return 0

  def files(self):
    """Returns a list of all WatchFile objects we are watching.

    This will return a list of all WatchFile objects associated with config
    files in the list of directories that we are currently watching.

    Returns:
      A list of all WatchConfig objects we are maintaining.
    """
    return [w for _, w in self._watch_files.itervalues()]

  def rescan(self):
    """Rescans all directories looking for files inside.

    This will walk all the directories listed when this class was created
    looking for configuration files. If new config files are found then
    a object will be created using the class passed in at init time. If a
    file that used to exist was deleted then the config object for it
    will also be deleted.
    """
    new_dirs, new_files = self._recurse_directory()

    # Old directories, unregister watches.
    for dir in set(self._watch_fds.iterkeys()).difference(new_dirs):
      self._unregister_inotify(dir)

    # New directories, register watches.
    for dir in new_dirs:
      self._register_inotify(dir)

    # Walk through all files that no longer exist.
    for file in set(self._watch_files).difference(new_files):
      logging.info('File deleted (%s): Removing its object.', file)
      del self._watch_files[file]

    for file in new_files:
      if file not in self._watch_files:
        w = self._watch_class(file)
        self._watch_files[file] = [None, w]
        logging.info('Found new file (%s): Making new object', file)
      t = self._watch_files[file]
      m = self._mtime(file)
      if t and t[0] != m:
        t[0] = m
        t[1].reload()

#!/usr/bin/python26

"""This file contains the classes needed to manage twitcher configs.

Author: Brady Catherman (brady@twitter.com)
"""

import logging
import os
import types

# twitcher module libs
import core
import inotify
import zkwrapper


class _NamespaceConfig(object):
  """This class is imported into the exec namespace as 'twitcher'.

  This class provides the configuration files access in and out of twitcher.
  It is intended that this class will be used as thought it was a module
  inside of the twitcher config files namespaces.

  Some examples:
    in the config: twitcher.RegisterWatch(...)
    in this class: _NamespaceConfig.RegisterWatch(...)

  This allows modules to configure themselves using full python without also
  being able to directly access twitcher internals.

  Args:
    config_file: The file this object should parse.
  """
  def __init__(self, config_file):
    self._configurations = []
    self._config_file = config_file

  def RegisterWatch(self, znode=None, action=None, pipe_stdin=None,
                    run_on_load=None, run_mode=None, description=None,
                    uid=None, gid=None, watch_type=None, notify_signal=None,
                    timeout=None):
    """Registers a watch and action on a given znode.

    This is the main function configuration files are expected to work with.
    A configuration file should call this script in order to register a
    basic znode watch and set the function that should be called when
    the node is updated.

    Args:
      znode: The node that will be watched for updates.
      action: The function that should be called when the node updates.
      pipe_stdin: Pipe the contents of the znode to stdin when the
                  'action' function run.
      run_on_load: Should the action function be run when the config file
                   is loaded or reloaded.
      run_mode: Defines what should happen if the znode updates while the
                script is still running. The options are:
            QUEUE: Queue the update and run the action once the current action
                   finishes. Note that if two watches trigger while the action
                   is currently running it will still only be run once.
            PARALLEL: Run the action every time it triggers regardless if
                      an action is already running.
            DISCARD: Ignore any watches that fire while the action script
                     is already running.
      description: The string description of this object. This will be used
                   for logging.
      uid: The user id to run the process as.
      gid: The group id to run the process as.
      watch_type: The type of watcher to set on the node. The options are:
            WATCH_DATA: Watches the node data for changes
            WATCH_CHILDREN: Watches for creation/deletion of child nodes
                            of the watched node
      notify_signal: The signal that will be sent to a child process when
                     the znode is modified. This only matters in QUEUE mode.
      timeout: The number of seconds to allow the process to run before
               killing it.

    Returns:
      Nothing.
    """
    assert type(znode) == types.StringType, (
        'RegisterWatch: znode must be a string.')
    assert (type(action) == types.FunctionType or
            type(action) == types.UnboundMethodType or
            type(action) == types.LambdaType), (
        'RegisterWatch: action must be a function, method or lambda.')
    assert pipe_stdin is None or type(pipe_stdin) == types.BooleanType, (
        'RegisterWatch: pipe_stdin must be one of True or False.')
    assert run_on_load is None or type(run_on_load) == types.BooleanType, (
        'RegisterWatch: run_on_load must be one of True of False.')
    assert (run_mode is None or run_mode is core.QUEUE or
            run_mode is core.PARALLEL or run_mode is core.DISCARD), (
        'RegisterWatch: run_mode is not one of QUEUE, PARALLEL or DISCARD.')
    assert description is None or type(description) == types.StringType, (
        'RegisterWatch: Description must be a string.')
    assert (uid is None or
            type(uid) == types.IntType or
            type(uid) == types.StringType), (
        'RegisterWatch: uid must be a number of a string.')
    assert (gid is None or
            type(gid) == types.IntType or
            type(gid) == types.StringType), (
        'RegisterWatch: gid must be a number of a string.')
    assert (watch_type is None or watch_type is core.WATCH_DATA or
            watch_type is core.WATCH_CHILDREN), (
        'RegisterWatch: watch_type is not one of WATCH_DATA or WATCH_CHILDREN')
    assert (notify_signal is None or
            (type(notify_signal) == types.IntType and
             notify_signal > 0 and notify_signal < 32)), (
        'RegisterWatch: notify_signal must be an int below 32.')
    assert (timeout is None or
            type(timeout) == types.IntType or
            type(timeout) == types.FloatType), (
        'RegisterWatch: timeout must be a number.')

    if description is None:
      description = '%s-%s' % (self._config_file, len(self._configurations) + 1)

    kwargs = {}
    kwargs['description'] = description
    if pipe_stdin is not None:
      kwargs['pipe_stdin'] = pipe_stdin
    if run_on_load is not None:
      kwargs['run_on_load'] = run_on_load
    if run_mode is not None:
      kwargs['run_mode'] = run_mode
    if uid is not None:
      kwargs['uid'] = uid
    if gid is not None:
      kwargs['gid'] = gid
    if notify_signal is not None:
      kwargs['notify_signal'] = notify_signal
    if timeout is not None:
      kwargs['timeout'] = timeout

    if watch_type is core.WATCH_CHILDREN:
        config = core.TwitcherChildrenObject(znode, action, **kwargs)
    else:
        config = core.TwitcherObject(znode, action, **kwargs)
    self._configurations.append(config)

  def Exec(self, command):
    """Returns a lambda that will execute the given command.

    This function is a simple tool intended to be used in configurations
    files. This will return a lambda so the user doesn't have to write
    a function that executes anything.

    Args:
      command: The command to run. If this is a string then the command will
               be run in a bash instance. If its a list then it will be
               executed directly.

    Returns:
      A Lambda that will run the given command.
    """
    if isinstance(command, str):
      command = ['/bin/sh', '-c', command]
    return (lambda: os.execlp(command[0], *command))

  def get_configurations(self):
    """Returns a list of all configurations registered.

    This returns the list of all configurations created while parsing the
    configuration file. A configuration is functionally a
    twitcher.core.TwitcherObject which watches a znode and runs a script when
    it changes. A single file can contain many configurations.

    Returns:
      A list of TwitcherObjects.
    """
    return self._configurations


class ConfigFile(inotify.WatchClass):
  """Manages a single twitcher config file.

  This is a basic wrapper around a specific configuration file which allows
  automatic reloaded when it changes on disk (via inotify) as well as status
  reporting and other useful functionality.

  Args:
    filename: The filename that this config should manage.
  """
  def __init__(self, filename):
    self._filename = filename
    self._config_objects = []

  def __del__(self):
    """Called when this object is garbage collected."""
    if logging:
      logging.warning('Unloading configs from: %s', self._filename)

  def reload(self):
    """Loads or reloads the config file from disk."""
    logging.warning('Loading configuration from %s', self._filename)
    namespace_config = _NamespaceConfig(self._filename)
    plugins = {}
    exec_globals = {
        'Exec': namespace_config.Exec,
        'RegisterWatch': namespace_config.RegisterWatch,
        'QUEUE': core.QUEUE,
        'PARALLEL': core.PARALLEL,
        'DISCARD': core.DISCARD,
        'WATCH_DATA': core.WATCH_DATA,
        'WATCH_CHILDREN': core.WATCH_CHILDREN,
        }
    try:
      execfile(self._filename, exec_globals, {})
      objects = namespace_config.get_configurations()
      for o in objects:
        o.init()
    except Exception, e:
      logging.error('Exception processing %s: %s' % (self._filename, e))
      return

    self._config_objects = objects
    logging.warning('Successfully loaded configs from %s', self._filename)

  def get_configurations(self):
    """Returns all configurations (TwitcherObjects) from this file."""
    return self._config_objects

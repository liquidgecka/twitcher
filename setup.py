#!/usr/bin/env python

import os
import platform

from distutils.core import setup

def get_version():
    """Returns the current version of twitcher."""
    if os.path.isdir(".git"):
        os.system('./version.sh > version.txt')
        f = open('version.txt')
    else:
        f = open('version.txt')
    version = ''.join(f.readlines()).rstrip()
    f.close()
    return version

data_files = [('/usr/share/twitcher', ['README'])]

if platform.dist()[0] == 'Ubuntu':
    data_files.append(('/etc/init', ['debian/upstart/twitcher.conf']))

if platform.dist()[0] in ['centos', 'redhat', 'debian']:
    data_files.append(('/etc/init.d', ['scripts/init.d/twitcher']))
    data_files.append(('/var/log/twitcher', ['.keep']))
    data_files.append(('/var/run/twitcher', ['.keep']))

setup(name='twitcher',
      author='Brady Catherman',
      author_email='github@gecka.us',
      data_files=data_files,
      description='A tool for watching Zookeeper nodes.',
      packages=['twitcher'],
      scripts=["scripts/twitcher"],
      url='http://github.com/liquidgecka/twitcher',
      version=get_version(),
     )

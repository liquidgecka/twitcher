#!/bin/bash

CURRENT=$(git describe --exact-match 2> /dev/null | egrep '^v[0-9]' )
PREVIOUS=$(git describe --abbrev=0 2> /dev/null | egrep '^v[0-9]' )
if [ "$CURRENT" != "" ] && ! git diff --shortstat | egrep -q '.' ; then
  # This commit is a version tag directly. That makes it realy easy.
  IFS='.' read -ra COMPS <<< "${CURRENT#v}"
  VERSION_MAJ="${COMPS[0]}"
  VERSION_MIN="${COMPS[1]}"
  VERSION_REV="${COMPS[2]}"
elif [ "$PREVIOUS" != "" ] ; then
  # Check to see if any files have changed. If so this becomes a pre-release
  # version. If files have changed in debian/ then the version remains the
  # same, and the release gets updated.
  if ! git diff --name-only "$PREVIOUS" | egrep -vq '^debian/' ; then
    IFS='.' read -ra COMPS <<< "${PREVIOUS#v}"
    VERSION_MAJ="${COMPS[0]}"
    VERSION_MIN="${COMPS[1]}"
    VERSION_REV="${COMPS[2]}"
  else
    IFS='.' read -ra COMPS <<< "${PREVIOUS#v}"
    VERSION_MAJ="${COMPS[0]}"
    VERSION_MIN="${COMPS[1]}"
    VERSION_REV="${COMPS[2]}-$(git rev-parse --short HEAD)"
  fi
fi

if [ -z "$VERSION_MAJ" -o -z "$VERSION_MIN" -o -z "$VERSION_REV" ] ; then
  echo "Can not find current version."
  exit 1
fi

while getopts "mnru" opt; do
  case $opt in
    m)
      echo "${VERSION_MAJ}"
      exit 0
      ;;
    n)
      echo "${VERSION_MIN}"
      exit 0
      ;;
    r)
      echo "${VERSION_REV}"
      exit 0
      ;;
    u)
      VERSION="$(head -n1 debian/changelog | awk -F'[()]' '{print $2}')"
      echo "${VERSION#*-}"
      exit 0
      ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      exit 1
      ;;
    esac
done

VERSION="${VERSION_MAJ}.${VERSION_MIN}.${VERSION_REV}"
echo $VERSION

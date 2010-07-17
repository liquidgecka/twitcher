#!/bin/bash

TMPDIR=$(mktemp -d)
trap 'rm -rf $TMPDIR' EXIT

mkdir -p "$TMPDIR/BUILD"
mkdir -p "$TMPDIR/RPMS"
mkdir -p "$TMPDIR/SOURCES"
mkdir -p "$TMPDIR/SPECS"

tar -cv --exclude '*.pyc' \
    init-twitcher bin twitcher > "$TMPDIR/SOURCES/twitcher.tar"
cp twitcher.spec "$TMPDIR/SPECS"

rpmbuild --define "_topdir $TMPDIR" -bb "$TMPDIR/SPECS/twitcher.spec"

RPMS=$(ls $TMPDIR/RPMS/x86_64)

mv "$TMPDIR/RPMS/x86_64/"*.rpm /tmp

echo "Placing the following rpms in /tmp:"
echo "$RPMS"


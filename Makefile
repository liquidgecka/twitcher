#!/usr/bin/make

# This make file is a convienence wrapper to allow easy building and
# Distributing of Twitcher versions.

# Get the current version.
VERSION=$(shell ./version.sh)
DEBIAN_RELEASE=$(shell ./version.sh -u)
DISTROS=lucid precise quantal saucy
GPG_KEY=brady@catherman.org

new_release:
	if [ "$(VER)" = "" ] ; then \
	  echo "Specify a version using VER=x" ; \
	  exit 1 ; \
	fi
	if echo "$(VER)" | egrep -vq '[0-9]+\.[0-9]+\.[0-9]+' ; then \
	  echo "Versions must be in the form: x.y.z" ; \
	  exit 1 ; \
	fi
	if git rev-parse "v$(VER)" > /dev/null 2>&1 ; then \
	  echo "Release already exists." ; \
	  exit 1 ; \
	fi
	if ! egrep -q "$(VER)" README ; then \
	  echo "Version doesn't exist in README." ; \
	  exit 1 ; \
	fi
	if git diff --shortstat | egrep -q '.' ; then \
	  echo "You have uncommited changes." ; \
	  exit 1 ; \
	fi
	gpg -bsau "$(GPG_KEY)" --output=/dev/null /dev/null
	git tag -s -u "$(GPG_KEY)" "v$(VER)" -m "Version $(VER)" HEAD
	dch -v "$(VER)-1"
	dch -r
	git commit -m 'Debian release $(VER)-1' debian/changelog
	make pypi debuild launchpad clean

debian_release:
	if git diff --shortstat | egrep -q '.' ; then \
	  echo "You have uncommited changes." ; \
	  exit 1 ; \
	fi
	dch -i
	dch -r
	git commit -m 'Updated debian release' debian/changelog
	make debuild launchpad clean

clean:
	[ ! -f version.txt.auto ] || rm -f version.txt.auto
	[ ! -f build ] || rm -rf build
	[ ! -f debian/files ] || rm -f debian/files
	[ ! -f debian/twitcher.debhelper.log ] || \
	    rm -f debian/twitcher.debhelper.log
	[ ! -f debian/twitcher.postinst.debhelper ] || \
	    rm -f debian/twitcher.postinst.debhelper
	[ ! -f debian/twitcher.prerm.debhelper ] || \
	    rm -f debian/twitcher.prerm.debhelper
	[ ! -f debian/twitcher.substvars ] || \
	    rm -f debian/twitcher.substvars
	[ ! -f debian/twitcher ] || rm -rf debian/twitcher

pypi:
	python setup.py sdist upload

debuild:
	mkdir -p build/dpkg
	[ -f "build/dpkg/twitcher_$(VERSION).orig.tar.gz" ] || ( \
	  git archive --format=tar --prefix="twitcher-$(VERSION)/" HEAD | \
	      ( cd "build/dpkg" && tar -x ) && \
	  echo "$(VER)" > "build/dpkg/twitcher-$(VERSION)/version.txt" && \
	  ( cd "build/dpkg" && \
	    tar -c twitcher-$(VERSION) | \
	    gzip > "twitcher_$(VERSION).orig.tar.gz" ) )
	cd "build/dpkg/twitcher-$(VERSION)" && debuild -S

launchpad: debuild
	dput \
	    ppa:liquidgecka/twitcher \
	    "build/dpkg/twitcher_$(VERSION)-$(DEBIAN_RELEASE)_source.changes"
	for distro in $(DISTROS) ; do \
	  backportpackage \
	      -d "$${distro}" \
	      --upload ppa:liquidgecka/twitcher \
	      "build/dpkg/twitcher_$(VERSION)-$(DEBIAN_RELEASE).dsc" ; \
	done

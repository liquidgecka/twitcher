%define VERSION  1.6
%define RELEASE  3

Name:           twitcher
Version:        %{VERSION}
Release:        %{RELEASE}
Summary:        A zookeeper watch daemon.
Group:          Tools/Twitcher
License:        Internal
Url:            http://github.com/twitter/twitcher
Source:         twitcher.tar

BuildRoot:      %{_tmppath}/%{name}-%{version}-%{release}-root

Requires:       python26
BuildRequires:  python26

%description
A basic daemon that watches znodes in ZooKeeper and runs scripts on the
system when they change.

%prep
rm -rf "${RPM_BUILD_DIR}"/twitcher
mkdir -p "${RPM_BUILD_DIR}"/twitcher
tar -C "${RPM_BUILD_DIR}"/twitcher -xvf "${RPM_SOURCE_DIR}"/twitcher.tar

%install
mkdir -p "$RPM_BUILD_ROOT/usr/sbin"
cp "${RPM_BUILD_DIR}"/twitcher/bin/twitcher "${RPM_BUILD_ROOT}/usr/sbin"

mkdir -p "$RPM_BUILD_ROOT/etc/init.d"
cp "${RPM_BUILD_DIR}"/twitcher/init-twitcher \
    "${RPM_BUILD_ROOT}/etc/init.d/twitcher"

mkdir -p "$RPM_BUILD_ROOT/usr/lib/python2.6/site-packages"
cp -rf "${RPM_BUILD_DIR}"/twitcher/twitcher \
    "${RPM_BUILD_ROOT}/usr/lib/python2.6/site-packages"

mkdir -p "${RPM_BUILD_ROOT}"/etc/twitcher

# Generate the proper .pyc and .pyo files.
/usr/lib/rpm/brp-python-bytecompile /usr/bin/python26

%clean
rm -rf "${RPM_BUILD_ROOT}"

%post
/sbin/chkconfig twitcher on
/etc/init.d/twitcher start

%preun
/sbin/chkconfig twitcher off
/etc/init.d/twitcher stop

%files
%defattr(-,root,root,-)
/etc/twitcher
/etc/init.d/twitcher
/usr/sbin/twitcher
/usr/lib/python2.6/site-packages/twitcher

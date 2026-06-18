# Lab-only FRR NETCONF/YANG image.
#
# This intentionally builds FRR with sysrepo support for Containerlab tests. It
# must not be used as a production packaging path for AS215932 routers.

FROM debian:sid-slim

ARG FRR_REF=stable/10.4
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      autoconf \
      automake \
      bison \
      build-essential \
      ca-certificates \
      cmake \
      flex \
      git \
      iproute2 \
      jq \
      libcap-dev \
      libc-ares-dev \
      libelf-dev \
      libjson-c-dev \
      libpcre2-dev \
      libprotobuf-c-dev \
      libreadline-dev \
      libsqlite3-dev \
      libsysrepo-dev \
      libtool \
      libyang3-dev \
      make \
      netopeer2 \
      openssh-client \
      pkg-config \
      protobuf-c-compiler \
      python3 \
      python3-pip \
      sqlite3 \
      sysrepo \
      sysrepo-modules \
      sysrepo-plugind \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system frr \
 && groupadd --system frrvty \
 && useradd --system --home-dir /var/run/frr --gid frr --groups frrvty --shell /usr/sbin/nologin frr \
 && useradd --create-home --shell /bin/bash netconf \
 && echo 'netconf:netconf' | chpasswd

RUN git clone --depth=1 --branch "${FRR_REF}" https://github.com/FRRouting/frr.git /usr/src/frr

WORKDIR /usr/src/frr
RUN ./bootstrap.sh \
 && ./configure \
      --prefix=/usr \
      --sbindir=/usr/lib/frr \
      --sysconfdir=/etc/frr \
      --localstatedir=/run/frr \
      --with-moduledir=/usr/lib/frr/modules \
      --with-yangmodelsdir=/usr/share/yang \
      --enable-user=frr \
      --enable-group=frr \
      --enable-vty-group=frrvty \
      --enable-vtysh \
      --enable-configfile-mask=0640 \
      --enable-logfile-mask=0640 \
      --enable-config-rollbacks \
      --enable-sysrepo \
      --disable-doc \
 && make -j"$(nproc)" \
 && make install

RUN python3 -m pip install --break-system-packages --no-cache-dir ncclient

COPY install-frr-yang-modules.sh /usr/local/sbin/install-frr-yang-modules.sh
RUN chmod +x /usr/local/sbin/install-frr-yang-modules.sh \
 && /usr/local/sbin/install-frr-yang-modules.sh

COPY entrypoint.sh /usr/local/sbin/as215932-netconf-entrypoint.sh
COPY netconf_smoke.py /usr/local/bin/as215932-netconf-smoke.py
RUN chmod +x /usr/local/sbin/as215932-netconf-entrypoint.sh /usr/local/bin/as215932-netconf-smoke.py \
 && mkdir -p /etc/frr /run/frr \
 && chown -R frr:frr /etc/frr /run/frr

EXPOSE 830/tcp
ENTRYPOINT ["/usr/local/sbin/as215932-netconf-entrypoint.sh"]

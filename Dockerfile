FROM registry.fedoraproject.org/fedora:39

RUN dnf -y install --setopt=install_weak_deps=False \
  python3 python3-pip python3-slackclient python3-requests \
  python3-pyyaml python3-cryptography && \
  dnf clean all

RUN pip install ghapi
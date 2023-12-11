FROM registry.fedoraproject.org/fedora:39

RUN dnf -y install --setopt=install_weak_deps=False \
  python3 python3-github3py python3-slackclient && \
  dnf clean all

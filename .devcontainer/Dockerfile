ARG ESPHOME_IMAGE=ghcr.io/esphome/esphome:stable
FROM ${ESPHOME_IMAGE}

ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=1000

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

USER root

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      file \
      git \
      iputils-ping \
      jq \
      less \
      nano \
      netcat-openbsd \
      openssh-client \
      procps \
      python3 \
      python3-pip \
      ripgrep \
      rsync \
      shellcheck \
      tree \
      unzip \
      usbutils \
      vim-tiny \
      xz-utils \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir --upgrade pyserial esptool \
 || python3 -m pip install --no-cache-dir --break-system-packages --upgrade pyserial esptool

RUN if ! getent group "${USER_GID}" >/dev/null; then \
      groupadd --gid "${USER_GID}" "${USERNAME}"; \
    fi \
 && if ! id -u "${USERNAME}" >/dev/null 2>&1; then \
      useradd --uid "${USER_UID}" --gid "${USER_GID}" -m -s /bin/bash "${USERNAME}"; \
    fi \
 && mkdir -p /cache/platformio /cache/pip /workspaces \
 && chown -R "${USER_UID}:${USER_GID}" /cache /workspaces

ENV PLATFORMIO_CORE_DIR=/cache/platformio
ENV PIP_CACHE_DIR=/cache/pip
ENV PATH=/home/${USERNAME}/.local/bin:${PATH}

USER ${USERNAME}
WORKDIR /workspaces/esp-codex-platform

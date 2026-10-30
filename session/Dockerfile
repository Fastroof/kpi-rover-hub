FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg python3-venv git \
      xfce4 xfce4-terminal dbus-x11 xauth x11-xserver-utils xkb-data x11-xkb-utils \
      fonts-dejavu-core \
      mesa-utils libgl1 libegl1 libglu1-mesa libgl1-mesa-dri \
      htop nano less procps \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://packagecloud.io/dcommander/turbovnc/gpgkey | gpg --dearmor -o /etc/apt/trusted.gpg.d/TurboVNC.gpg \
 && curl -fsSL https://packagecloud.io/dcommander/virtualgl/gpgkey | gpg --dearmor -o /etc/apt/trusted.gpg.d/VirtualGL.gpg \
 && curl -fsSL -o /etc/apt/sources.list.d/TurboVNC.list https://raw.githubusercontent.com/TurboVNC/repo/main/TurboVNC.list \
 && curl -fsSL -o /etc/apt/sources.list.d/VirtualGL.list https://raw.githubusercontent.com/VirtualGL/repo/main/VirtualGL.list \
 && apt-get update && apt-get install -y --no-install-recommends turbovnc virtualgl \
 && rm -rf /var/lib/apt/lists/*

RUN V=$(curl -fsSL https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest | grep -F tag_name | cut -d'"' -f4) \
 && curl -fsSL -o /tmp/ros2-apt-source.deb \
      "https://github.com/ros-infrastructure/ros-apt-source/releases/download/$V/ros2-apt-source_$V.noble_all.deb" \
 && apt-get install -y /tmp/ros2-apt-source.deb && rm /tmp/ros2-apt-source.deb \
 && apt-get update && apt-get install -y \
      ros-jazzy-desktop ros-jazzy-ros-gz ros-jazzy-teleop-twist-keyboard \
      python3-colcon-common-extensions python3-vcstool python3-rosdep build-essential \
      python3-pyqt5 \
 && rosdep init \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://code-server.dev/install.sh | sh \
 && curl -fsSL "https://code.visualstudio.com/sha/download?build=stable&os=cli-alpine-x64" | tar -xz -C /usr/local/bin

ARG JUPYTERHUB_VERSION
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir \
      "jupyterhub==${JUPYTERHUB_VERSION}" \
      "jupyterlab==4.6.5" \
      "jupyter-remote-desktop-proxy==3.0.1" \
      "jupyter-vscode-proxy==0.7"

# UID 1000 is taken by "ubuntu" in ubuntu:24.04
RUN userdel -r ubuntu && useradd -m -u 1000 -s /bin/bash jovyan

RUN printf '%s\n' 'source /opt/ros/jazzy/setup.bash' \
      '[ -f ~/ros_ws/install/setup.bash ] && source ~/ros_ws/install/setup.bash' >> /etc/bash.bashrc

# System python3 (PyQt5, ROS) before the Jupyter venv
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/TurboVNC/bin:/opt/VirtualGL/bin:/opt/venv/bin \
    SHELL=/bin/bash

# Docker copies ~/work into a new user volume on first login.
# BUILD_TIME changes every build, so git clones below always fetch fresh code.
ARG BUILD_TIME
ARG ROS_WS_REPO
ARG KPI_ROVER_BRANCH
RUN git clone "$ROS_WS_REPO" /home/jovyan/work/ros_ws \
 && cd /home/jovyan/work/ros_ws \
 && vcs import src < kpi-rover.repos \
 && git -C src/kpi_rover checkout "$KPI_ROVER_BRANCH" \
 && apt-get update \
 && rosdep update --rosdistro jazzy \
 && rosdep install --from-paths src --ignore-src -r -y --rosdistro jazzy \
 && rm -rf /var/lib/apt/lists/* \
 && chown -R jovyan:jovyan /home/jovyan

# Overridden by GUI_DIR from the host
ARG GUI_REPO
RUN git clone "$GUI_REPO" /opt/kpi_rover_gui \
 && ln -s /opt/kpi_rover_gui/kpi-rover-gui /usr/local/bin/kpi-rover-gui \
 && printf '%s\n' '[Desktop Entry]' 'Type=Application' 'Name=KPI Rover' \
      'Exec=/opt/VirtualGL/bin/vglrun -d egl /usr/local/bin/kpi-rover-gui' \
      'Icon=applications-science' 'Terminal=false' 'Categories=Science;' \
      > /usr/share/applications/kpi-rover.desktop

USER jovyan
WORKDIR /home/jovyan

# GUI expects ~/ros_ws; GUI settings and VS Code login persist in ~/work
RUN mkdir -p ~/.colcon && printf 'build:\n  symlink-install: true\n' > ~/.colcon/defaults.yaml \
 && rosdep update --rosdistro jazzy \
 && cd ~/work/ros_ws && bash -c 'source /opt/ros/jazzy/setup.bash && colcon build' \
 && ln -s work/ros_ws ~/ros_ws \
 && mkdir -p ~/work/.config/kpi_rover_gui ~/work/.vscode ~/work/.vscode-server ~/.config ~/Desktop \
 && printf '[General]\nsim_gazebo_window=false\n' > ~/work/.config/kpi_rover_gui/kpi_rover_gui.conf \
 && ln -s ../work/.config/kpi_rover_gui ~/.config/kpi_rover_gui \
 && ln -s work/.vscode ~/.vscode \
 && ln -s work/.vscode-server ~/.vscode-server \
 && install -m 755 /usr/share/applications/kpi-rover.desktop ~/Desktop/

COPY --chmod=755 gpu-test kpi-vscode start-session /usr/local/bin/
CMD ["start-session"]

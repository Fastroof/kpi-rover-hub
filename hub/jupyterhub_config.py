import hmac
import os
import sys

import docker
from jupyterhub.auth import Authenticator

c = get_config()  # noqa: F821
env = os.environ


def names(var):
    return {n.strip() for n in env.get(var, "").split(",") if n.strip()}


class UsersFile(Authenticator):
    """Lines "<user> <password>" in the users file; re-read on every login."""

    async def authenticate(self, handler, data):
        with open("/srv/jupyterhub/users") as f:
            users = dict(p for p in map(str.split, f) if len(p) == 2)
        name = data["username"]
        if name in users and hmac.compare_digest(users[name].encode(), data["password"].encode()):
            return name


c.JupyterHub.bind_url = "http://:8000"
c.JupyterHub.hub_ip = "kpi-rover-hub"
c.JupyterHub.hub_port = 8080
c.JupyterHub.db_url = "sqlite:////data/jupyterhub.sqlite"
c.JupyterHub.cookie_secret_file = "/data/jupyterhub_cookie_secret"
c.JupyterHub.cleanup_servers = False
c.JupyterHub.active_server_limit = int(env["MAX_SESSIONS"])  # 0 = no limit

c.JupyterHub.authenticator_class = UsersFile
c.Authenticator.allow_all = True
c.Authenticator.admin_users = names("ADMIN_USERS")

c.JupyterHub.spawner_class = "dockerspawner.DockerSpawner"
c.DockerSpawner.image = env["SESSION_IMAGE"]
c.DockerSpawner.prefix = "kpi-rover-session"
c.DockerSpawner.network_name = "kpi-rover-hub"
c.DockerSpawner.use_internal_ip = True
c.DockerSpawner.remove = True
c.DockerSpawner.notebook_dir = "/home/jovyan/work"
c.DockerSpawner.volumes = {"kpi-rover-work-{username}": "/home/jovyan/work"}
if env.get("GUI_DIR"):
    c.DockerSpawner.volumes[env["GUI_DIR"]] = {"bind": "/opt/kpi_rover_gui", "mode": "ro"}
c.Spawner.default_url = "/lab"
c.Spawner.start_timeout = 120
c.Spawner.http_timeout = 120

c.DockerSpawner.cpu_limit = float(env["CPU_LIMIT"])
c.DockerSpawner.mem_limit = env["MEM_LIMIT"]


def unlimited(spawner):
    if spawner.user.name in names("UNLIMITED_USERS"):
        spawner.cpu_limit = None
        spawner.mem_limit = None


c.Spawner.pre_spawn_hook = unlimited

c.DockerSpawner.environment = {
    "NVIDIA_DRIVER_CAPABILITIES": "all",  # "graphics" is required for OpenGL/EGL
    "NVIDIA_VISIBLE_DEVICES": "all",
    "ROS_AUTOMATIC_DISCOVERY_RANGE": "LOCALHOST",
    "GZ_PARTITION": lambda spawner: spawner.user.name,
    **{k: env[k] for k in ("ROS_WS_REPO", "KPI_ROVER_BRANCH", "GUI_REPO", "HUB_URL")},  # for kpi-setup, kpi-ssh
}
c.DockerSpawner.extra_host_config = {
    "device_requests": [docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])],
    "security_opt": ["seccomp=unconfined"],  # required by the desktop
    "shm_size": "1g",
    "init": True,  # reaps zombies (e.g. of the ros2 daemon): jupyterhub-singleuser as PID 1 doesn't
}

# Desktop reports activity only every ~5 min
c.JupyterHub.last_activity_interval = 60
c.JupyterHub.load_roles = [{
    "name": "idle-culler",
    "scopes": ["list:users", "read:users:activity", "read:servers", "delete:servers"],
    "services": ["idle-culler"],
}, {
    "name": "load",  # last activity of the sessions
    "scopes": ["list:users", "read:users:activity", "read:servers"],
    "services": ["load"],
}, {
    "name": "server",  # default scopes + tokens, so kpi-ssh can create one
    "scopes": ["users:activity!user", "access:servers!server", "tokens!user"],
}]
c.JupyterHub.services = [{
    "name": "idle-culler",
    "command": [sys.executable, "-m", "jupyterhub_idle_culler",
                f"--timeout={env['IDLE_TIMEOUT']}", "--cull-every=60"],
}, {
    # System load page at /services/load/: admins only (access:services is in the admin role)
    "name": "load",
    "url": "http://kpi-rover-hub:10101",
    "command": [sys.executable, "/srv/jupyterhub/load/service.py"],
    "environment": {"PATH": env["PATH"]},
    "oauth_no_confirm": True,
    "display": True,
}]

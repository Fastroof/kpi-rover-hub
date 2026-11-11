#!/usr/bin/env python3
"""System load page: CPU, RAM, disk, GPU of the host and of every container.

A JupyterHub service at /services/load/, for admins (the hub's access:services scope).
Samples every INTERVAL seconds and keeps KEEP seconds in memory (lost on hub restart).
GPU per container needs the host PID namespace (pid: host in compose.yaml):
nvidia-smi reports host PIDs, and /proc/<pid>/cgroup names the container.
"""
import asyncio
import collections
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import docker
from jupyterhub.services.auth import HubOAuthCallbackHandler, HubOAuthenticated
from jupyterhub.utils import url_path_join
from tornado import ioloop, web
from tornado.httpclient import AsyncHTTPClient

INTERVAL = 5
KEEP = 6 * 3600
USERS_EVERY = 30
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
PREFIX = os.environ["JUPYTERHUB_SERVICE_PREFIX"]
SESSION_PREFIX = "kpi-rover-session-"
CONTAINER_ID = re.compile(r"[0-9a-f]{64}")


def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def num(text):
    try:
        return float(text)
    except ValueError:  # "-", "[N/A]"
        return 0.0


class Collector:
    def __init__(self):
        self.docker = docker.from_env()
        self.samples = collections.deque(maxlen=KEEP // INTERVAL)
        self.history = {}    # container id → deque of [t, cores, bytes, gpu MB]
        self.current = []    # latest row per running container
        self.users = {}      # hub user name → {last_activity, started}
        self.prev_cpu = None
        self.prev_ctr = {}   # container id → (t, cpu ns)
        self.gpus = []

    # Host

    @staticmethod
    def host_cpu_times():
        with open("/proc/stat") as f:
            values = [int(v) for v in f.readline().split()[1:]]
        idle = values[3] + values[4]  # idle + iowait
        return sum(values), idle

    @staticmethod
    def host_memory():
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, value = line.split(":", 1)
                info[key] = int(value.split()[0]) * 1024
        return info["MemTotal"], info["MemTotal"] - info["MemAvailable"]

    @staticmethod
    def disk():
        st = os.statvfs("/")  # overlay of /var/lib/docker: the host's Docker disk
        total = st.f_blocks * st.f_frsize
        return {"total": total, "used": total - st.f_bfree * st.f_frsize, "free": st.f_bavail * st.f_frsize}

    # GPU

    def gpu_totals(self):
        out = run("nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                  "--format=csv,noheader,nounits")
        if out is None:
            return None
        gpus = []
        for line in out.strip().splitlines():
            name, util, used, total, temp = [v.strip() for v in line.split(",")]
            gpus.append({"name": name, "util": num(util), "mem": num(used), "mem_total": num(total), "temp": num(temp)})
        return gpus

    @staticmethod
    def gpu_processes():
        """Host PID → (sm %, framebuffer MB), summed over GPUs."""
        out = run("nvidia-smi", "pmon", "-c", "1", "-s", "um")
        procs = {}
        if not out:
            return procs
        columns = None
        for line in out.splitlines():
            if line.startswith("#"):
                columns = columns or line[1:].split()  # first header line: gpu pid type sm mem ... fb ...
                continue
            fields = line.split()
            if not columns or len(fields) < len(columns) - 1 or not fields[1].isdigit():
                continue
            row = dict(zip(columns, fields))
            sm, fb = procs.get(int(row["pid"]), (0.0, 0.0))
            procs[int(row["pid"])] = (sm + num(row.get("sm", "-")), fb + num(row.get("fb", "-")))
        return procs

    @staticmethod
    def container_of(pid):
        try:
            with open(f"/proc/{pid}/cgroup") as f:
                match = CONTAINER_ID.search(f.read())
        except OSError:
            return None
        return match and match.group(0)

    # Containers

    @staticmethod
    def describe(name):
        if name.startswith(SESSION_PREFIX):
            return "session", name[len(SESSION_PREFIX):]
        if name == "kpi-rover-arena":
            return "arena", "arena"
        if name == "kpi-rover-hub":
            return "hub", "hub"
        return "other", name

    @staticmethod
    def limits(attrs):
        hc = attrs.get("HostConfig", {})
        cpus = None
        if hc.get("NanoCpus"):
            cpus = hc["NanoCpus"] / 1e9
        elif hc.get("CpuQuota", 0) > 0:
            cpus = hc["CpuQuota"] / (hc.get("CpuPeriod") or 100000)
        return cpus, hc.get("Memory") or None

    def collect(self):
        t = time.time()
        if self.samples and t < self.samples[-1]["t"]:  # the clock was set back: the history is in the future
            self.samples.clear()
            self.history.clear()
            self.prev_ctr.clear()
        total, idle = self.host_cpu_times()
        cpu = 0.0
        if self.prev_cpu:
            d_total, d_idle = total - self.prev_cpu[0], idle - self.prev_cpu[1]
            cpu = 100.0 * (d_total - d_idle) / d_total if d_total else 0.0
        self.prev_cpu = (total, idle)
        mem_total, mem_used = self.host_memory()

        gpus = self.gpu_totals()
        if gpus is not None:
            self.gpus = [{"name": g["name"], "mem_total": g["mem_total"]} for g in gpus]
        gpu_by_ctr = collections.defaultdict(lambda: [0.0, 0.0, 0])
        if gpus:
            for pid, (sm, fb) in self.gpu_processes().items():
                entry = gpu_by_ctr[self.container_of(pid) or "host"]
                entry[0] += sm
                entry[1] += fb
                entry[2] += 1

        rows, seen = [], set()
        for c in self.docker.containers.list():
            try:
                stats = c.stats(stream=False, one_shot=True)
            except docker.errors.APIError:
                continue
            seen.add(c.id)
            ns = stats.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0)
            prev = self.prev_ctr.get(c.id)
            cores = (ns - prev[1]) / 1e9 / (t - prev[0]) if prev and ns >= prev[1] else 0.0
            self.prev_ctr[c.id] = (t, ns)
            ms = stats.get("memory_stats", {})
            mem = max(0, ms.get("usage", 0) - ms.get("stats", {}).get("inactive_file", 0))
            sm, fb, nproc = gpu_by_ctr.get(c.id, (0.0, 0.0, 0))
            kind, who = self.describe(c.name)
            cpu_limit, mem_limit = self.limits(c.attrs)
            hist = self.history.setdefault(c.id, collections.deque(maxlen=KEEP // INTERVAL))
            hist.append([round(t, 1), round(cores, 3), mem, round(fb)])
            rows.append({
                "id": c.id, "name": c.name, "kind": kind, "who": who,
                "started": c.attrs.get("State", {}).get("StartedAt"),
                "cpu": round(cores, 3), "cpu_limit": cpu_limit,
                "mem": mem, "mem_limit": mem_limit,
                "gpu": round(sm), "gpu_mem": round(fb), "gpu_procs": nproc,
            })
        for gone in set(self.history) - seen:
            del self.history[gone]
            self.prev_ctr.pop(gone, None)
        self.current = rows

        sample = {"t": round(t, 1), "cpu": round(cpu, 1), "mem": mem_used, "mem_total": mem_total}
        if gpus:
            sample["gpu"] = round(sum(g["util"] for g in gpus) / len(gpus), 1)
            sample["gpu_mem"] = sum(g["mem"] for g in gpus)
            sample["gpu_temp"] = max(g["temp"] for g in gpus)
            outside = gpu_by_ctr.get("host")
            sample["gpu_host_mem"] = outside[1] if outside else 0
        self.samples.append(sample)

    def snapshot(self, since):
        return {
            "now": time.time(),
            "interval": INTERVAL,
            "keep": KEEP,
            "cpus": os.cpu_count(),
            "disk": self.disk(),
            "gpus": self.gpus,
            "samples": [s for s in self.samples if s["t"] > since],
            "containers": [dict(r, hist=[h for h in self.history.get(r["id"], ()) if h[0] > since])
                           for r in self.current],
            "users": self.users,
        }

    # Hub: last activity of the sessions

    async def poll_users(self):
        url = url_path_join(os.environ["JUPYTERHUB_API_URL"], "users")
        headers = {"Authorization": f"token {os.environ['JUPYTERHUB_API_TOKEN']}"}
        while True:
            try:
                resp = await AsyncHTTPClient().fetch(url, headers=headers)
                users = {}
                for u in json.loads(resp.body):
                    server = (u.get("servers") or {}).get("", {})
                    users[u["name"]] = {"last_activity": server.get("last_activity") or u.get("last_activity"),
                                        "admin": u.get("admin", False)}
                self.users = users
            except Exception as e:  # noqa: BLE001 - keep sampling the rest
                print(f"load: hub users: {e}", flush=True)
            await asyncio.sleep(USERS_EVERY)

    async def loop(self):
        pool = ThreadPoolExecutor(1)
        loop = asyncio.get_running_loop()
        while True:
            start = time.monotonic()
            try:
                await loop.run_in_executor(pool, self.collect)
            except Exception as e:  # noqa: BLE001
                print(f"load: sample: {e}", flush=True)
            await asyncio.sleep(max(0.5, INTERVAL - (time.monotonic() - start)))


class Page(HubOAuthenticated, web.RequestHandler):
    @web.authenticated
    def get(self):
        self.set_header("Content-Type", "text/html; charset=utf-8")
        with open(PAGE, encoding="utf-8") as f:
            # fetch() is a cors request: the hub accepts its cookie only with the XSRF token
            self.write(f.read().replace("{{xsrf}}", self.xsrf_token.decode()))


class Data(HubOAuthenticated, web.RequestHandler):
    def initialize(self, collector):
        self.collector = collector

    def get(self):
        # No redirect to the login: a fetch would follow it in a loop
        if not self.current_user:
            raise web.HTTPError(403)
        since = float(self.get_argument("since", str(-KEEP)))
        if since <= 0:  # relative to the server clock: the browser's may differ
            since += time.time()
        self.set_header("Cache-Control", "no-store")
        self.write(self.collector.snapshot(since))


async def main():
    collector = Collector()
    app = web.Application([
        (PREFIX.rstrip("/"), web.RedirectHandler, {"url": PREFIX}),
        (PREFIX, Page),
        (url_path_join(PREFIX, "api/data"), Data, {"collector": collector}),
        (url_path_join(PREFIX, "oauth_callback"), HubOAuthCallbackHandler),
    ], cookie_secret=os.urandom(32))
    app.listen(int(os.environ.get("LOAD_PORT", "10101")), address="0.0.0.0")
    asyncio.create_task(collector.loop())
    asyncio.create_task(collector.poll_users())
    await asyncio.Event().wait()


if __name__ == "__main__":
    ioloop.IOLoop.current().run_sync(main)

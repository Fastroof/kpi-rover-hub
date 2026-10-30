FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm \
 && npm install -g configurable-http-proxy@5.3.0 \
 && rm -rf /var/lib/apt/lists/*

ARG JUPYTERHUB_VERSION
RUN pip install --no-cache-dir \
      "jupyterhub==${JUPYTERHUB_VERSION}" \
      "dockerspawner==14.0.0" \
      "jupyterhub-idle-culler==2.0.0"

WORKDIR /srv/jupyterhub
CMD ["jupyterhub"]

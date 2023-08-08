#!/bin/bash
# Build and push dockers to Docker from dev environment.

RED_COLOR_ESCAPE='\033[0;31m'
NO_COLOR_ESCAPE='\033[0m'

echo -e "${RED_COLOR_ESCAPE}WARNING: This setup script will overwrite any existing changes in .env.${NO_COLOR_ESCAPE}" 
read -p "Enter version of new build: " version
read -p "Enter docker username:" username

docker compose -f ./docker-compose-deploy.yml -p barrel build barrel_venv_image
docker compose -f ./docker-compose-deploy.yml -p barrel build celery_worker barrel --no-cache

docker tag barrel-barrel ${username}/barrel-dev:barrel-${version}
docker push ${username}/barrel-dev:barrel-${version}

docker tag barrel-celery_worker ${username}/barrel-dev:celery_worker-${version}
docker push ${username}/barrel-dev:celery_worker-${version}
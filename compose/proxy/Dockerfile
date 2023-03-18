FROM nginxinc/nginx-unprivileged:1-alpine
#TODO: Edit maintainer to email or website link
LABEL maintainer="barcode_identifier_dev"

COPY ./default.conf.tpl /etc/nginx/default.conf.tpl 
COPY ./uwsgi_params /etc/nginx/uwsgi_params 
COPY ./run.sh /run.sh

# Listen to port 8000
ENV LISTEN_PORT=8000
# Name of service running Django app
ENV APP_HOST=barcode_identifier_api
# Run on port 9000
ENV APP_PORT=9000

USER root

# make folder for static 
RUN mkdir -p /vol/static && \
    chmod 755 /vol/static && \
    # make empty file 
    touch /etc/nginx/conf.d/default.conf && \
    # change ownership to nginx user 
    chown nginx:nginx /etc/nginx/conf.d/default.conf && \
    # allow script to execute
    chmod +x /run.sh

VOLUME /vol/static

# switch to nginx user
USER nginx 

CMD ["./run.sh"]
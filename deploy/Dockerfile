# One container, one job: serve the dividend dashboard over loopback.
FROM nginx:1.27-alpine

RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/dividend.conf

# The site files are bind-mounted at run time (see compose) so a scheduled
# fetch updates what is served without rebuilding this image.
RUN mkdir -p /srv/site

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/index.html >/dev/null 2>&1 || exit 1

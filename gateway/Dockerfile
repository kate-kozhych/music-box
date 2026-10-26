# syntax=docker/dockerfile:1
# Gateway: Nginx + static files. The SPA build stage is added with B-32;
# until then a static landing page links to the API docs.
FROM nginx:1.28-alpine
COPY gateway/nginx.conf /etc/nginx/conf.d/default.conf
COPY gateway/static/ /usr/share/nginx/html/
EXPOSE 80
RUN chmod -R a+rX /usr/share/nginx/html /etc/nginx/conf.d

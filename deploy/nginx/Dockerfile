# front nginx — 라우팅 전용(/console→api, /→web). base=alpine(busybox wget 내장 → healthcheck용, M2-1).
FROM nginx:alpine
COPY default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

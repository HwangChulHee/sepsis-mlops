# 운영 콘솔 웹 이미지 — 멀티스테이지: node로 vite build → nginx:alpine에 dist/ 정적 서빙.
# 런타임 nginx 설정은 K8s ConfigMap(sepsis-console-web-nginx)이
# /etc/nginx/conf.d/default.conf 로 덮어쓴다(console-web.yaml). 이미지에도 동일 SPA 폴백을
# 구워 standalone(쿠버 밖) 실행 시에도 동작하게 한다.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build           # tsc(src) + vite build → dist/

# nginx-unprivileged: 비-root(uid 101) 로 돌고 80 대신 8080 을 듣는다(K8s securityContext
# runAsNonRoot 와 일관). 일반 nginx:alpine 은 master 가 root 라 runAsNonRoot 에서 부팅 실패.
FROM nginxinc/nginx-unprivileged:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# console-web.yaml 의 ConfigMap 과 동일한 SPA 폴백(standalone 폴백용 — 런타임엔 ConfigMap이 덮어씀).
# listen 8080 — 비-root 라 80 바인드 불가. conf 쓰기는 USER 전환 전 수행.
USER root
RUN printf 'server {\n  listen 8080;\n  root /usr/share/nginx/html;\n  index index.html;\n  location / {\n    try_files $uri $uri/ /index.html;\n  }\n}\n' > /etc/nginx/conf.d/default.conf
USER 101
EXPOSE 8080

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

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# console-web.yaml 의 ConfigMap 과 동일한 SPA 폴백(standalone 폴백용 — 런타임엔 ConfigMap이 덮어씀)
RUN printf 'server {\n  listen 80;\n  root /usr/share/nginx/html;\n  index index.html;\n  location / {\n    try_files $uri $uri/ /index.html;\n  }\n}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80

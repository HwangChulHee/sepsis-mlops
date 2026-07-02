#!/bin/bash
# N축 부하 sweep (cores=2 고정) — 칸마다 서버 재시작(M-r2-1)+프리웜(m-r3-1).
set -u
cd /home/hch/sepsis-mlops
export PYTHONPATH="/home/hch/sepsis-mlops/src:/home/hch/sepsis-mlops"
COMPOSE="docker compose -f deploy/docker-compose.yml --project-directory ."
OUT=loadtest/results
RUNTIME=45s
SUMMARY=$OUT/summary.tsv
echo -e "N\treqs\tfails\trps\tp50\tp95\tp99\tmem_MiB" > "$SUMMARY"

wait_healthy() {
  for i in $(seq 1 60); do
    [ "$(docker inspect --format '{{.State.Health.Status}}' sepsis-serving 2>/dev/null)" = "healthy" ] && return 0
    sleep 2
  done
  return 1
}
prewarm() {
  curl -s -X POST http://localhost:8000/predict -H 'Content-Type: application/json' \
   -d '{"patient_id":"warmup","features":{"HR":88,"O2Sat":97,"Temp":37,"SBP":120,"MAP":80,"DBP":66,"Resp":18,"Age":64,"Gender":1}}' >/dev/null
}

for N in 1 10 50 200 500 1000; do
  echo "### CELL N=$N — restart serving"
  $COMPOSE restart serving >/dev/null 2>&1
  wait_healthy || { echo "N=$N serving not healthy"; continue; }
  prewarm
  ramp=$N; [ "$N" -gt 100 ] && ramp=200
  echo "### CELL N=$N — locust $RUNTIME (ramp=$ramp)"
  uv run locust -f loadtest/locustfile.py --host http://localhost:8000 \
    --headless -u "$N" -r "$ramp" --run-time "$RUNTIME" --reset-stats \
    --csv "$OUT/n$N" --only-summary >/dev/null 2>&1
  mem=$(docker stats --no-stream --format '{{.MemUsage}}' sepsis-serving | awk '{print $1}')
  # _stats.csv 의 Aggregated 행에서 지표 추출
  row=$(grep -E '^"?Aggregated"?,|,Aggregated,' "$OUT/n${N}_stats.csv" | tail -1)
  # locust csv columns: Type,Name,Request Count,Failure Count,Median,Average,Min,Max,...,Requests/s,...,50%,...,95%,...,99%,...
  reqs=$(echo "$row" | awk -F, '{print $3}')
  fails=$(echo "$row" | awk -F, '{print $4}')
  echo -e "$N\t$reqs\t$fails\t?\t?\t?\t?\t$mem" >> "$SUMMARY"
  echo "### CELL N=$N done: reqs=$reqs fails=$fails mem=$mem"
done
echo "### SWEEP COMPLETE"

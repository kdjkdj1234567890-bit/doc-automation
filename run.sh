#!/usr/bin/env bash
# 견적서·보고서 자동 생성기 실행 스크립트 (시연용 프로토타입)
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3이 필요합니다."; exit 1
fi

python3 -c "import flask" 2>/dev/null || pip install --break-system-packages -r requirements.txt

echo "==================================================="
echo " 견적서·보고서 자동 생성기 (시연용 프로토타입)"
echo " 브라우저에서 열기: http://127.0.0.1:5000"
echo " 종료: Ctrl+C"
echo "==================================================="
python3 app.py

FROM python:3.12-slim
# Garmin公式FIT SDKのインストール
RUN pip install --no-cache-dir --upgrade garmin-fit-sdk
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]


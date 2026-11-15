FROM python:3.12-slim
# Cross-kind source links need FIT timestamps even while rendering sample posts.
RUN pip install --no-cache-dir --upgrade garmin-fit-sdk
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]


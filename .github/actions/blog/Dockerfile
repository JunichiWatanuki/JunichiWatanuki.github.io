FROM python:3.12-slim
# Keep these versions in sync with scripts/requirements.txt.
RUN pip install --no-cache-dir cmarkgfm==2025.10.22 github-slugger==0.0.3 nh3==0.3.2
# Cross-kind source links need FIT timestamps even while rendering blog posts.
RUN pip install --no-cache-dir --upgrade garmin-fit-sdk
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]


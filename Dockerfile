FROM python:3.11.15-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    ffmpeg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY ./requirements.txt /src/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY ./src .

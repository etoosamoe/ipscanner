FROM python:3.9-slim-buster

WORKDIR /code
COPY . /code
RUN apt-get -qq update && \
    apt-get install -y iputils-ping

RUN pip install --no-cache-dir -r requirements.txt
CMD python ipscan.py
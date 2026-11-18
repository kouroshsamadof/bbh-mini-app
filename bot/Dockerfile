FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# دیتابیس در /data نگه‌داری می‌شود (با volume وصل می‌شود تا با ری‌استارت از بین نرود)
RUN mkdir -p /data
ENV DB_PATH=/data/bot_data.db
VOLUME /data

CMD ["python", "-u", "bot.py"]

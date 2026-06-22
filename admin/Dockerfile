FROM python:3.12-slim

WORKDIR /app

COPY admin/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY admin/app ./app
COPY shared ./shared

EXPOSE 4003

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "4003"]

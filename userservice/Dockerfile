FROM python:3.12-slim

WORKDIR /app

COPY userservice/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY userservice/app ./app
COPY shared ./shared

EXPOSE 4001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "4001"]

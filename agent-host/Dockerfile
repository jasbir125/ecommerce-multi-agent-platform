FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV PYTHONPATH=/srv PYTHONUNBUFFERED=1
EXPOSE 8092
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8092"]

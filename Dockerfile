# Single-stage: the app has no build step and only one heavy wheel (numpy).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Build the database at image-build time so the container starts ready to serve.
# Nothing here reaches the network: the CSV and the geocoded gazetteer are both in the
# repo, which is the whole point of doing the expensive work offline.
RUN python manage.py migrate --noinput \
 && python manage.py load_fuel_prices

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

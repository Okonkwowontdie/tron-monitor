FROM python:3.11-slim

WORKDIR /app

# Copy all your repo files to the container
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "monitor.py"]

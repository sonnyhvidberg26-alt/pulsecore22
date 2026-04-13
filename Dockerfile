FROM python:3.11-slim

WORKDIR /app

# Copy all files first
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Make start script executable
RUN chmod +x start.sh

# Expose port for web server (if needed)
EXPOSE 5000

# Start the application
CMD ["./start.sh"]

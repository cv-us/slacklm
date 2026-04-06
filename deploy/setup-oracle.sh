#!/bin/bash
# Oracle Cloud Free Tier VM setup script for SlackLM
# Run this on a fresh Ubuntu 22.04/24.04 ARM VM
set -e

echo "=== SlackLM: Oracle Cloud VM Setup ==="

# Update system
echo "Updating system packages..."
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker
echo "Installing Docker..."
sudo apt-get install -y docker.io docker-compose-v2

# Enable Docker on boot
sudo systemctl enable docker
sudo systemctl start docker

# Add current user to docker group (avoids needing sudo for docker commands)
sudo usermod -aG docker "$USER"

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Log out and back in (for docker group permissions)"
echo "  2. Clone the repo:  git clone https://github.com/cv-us/slacklm.git"
echo "  3. cd slacklm"
echo "  4. Copy your config files from local machine:"
echo "     scp .env ubuntu@<this-vm-ip>:~/slacklm/.env"
echo "     scp storage_state.json ubuntu@<this-vm-ip>:~/slacklm/storage_state.json"
echo "     scp config/channels.yaml ubuntu@<this-vm-ip>:~/slacklm/config/channels.yaml"
echo "  5. Build and start:  docker compose up -d --build"
echo "  6. Check logs:       docker compose logs -f"

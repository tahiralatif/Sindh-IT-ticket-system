module.exports = {
  apps: [{
    name: 'sindh-tickets',
    script: '/root/sindh-it-ticket-system/venv/bin/uvicorn',
    args: 'app.main:app --host 127.0.0.1 --port 8004',
    cwd: '/root/sindh-it-ticket-system',
    interpreter: 'none',
    env: {
      NODE_ENV: 'production'
    }
  }]
};

# botdiscord

Script nodriver qui parcourt une liste Disboard, suit les liens "Rejoindre" vers les invitations Discord et peut cliquer "Accepter l'invitation" (`--accept`).

```bash
pip install nodriver
python disboard_scrape_nodriver.py --login           # connexion Discord (une fois)
python disboard_scrape_nodriver.py --accept --max=10
```

Limites : le challenge Cloudflare de pleine page peut demander un clic manuel ; Discord peut refuser selon le groupe d'age du compte. L'adhesion automatique peut contrevenir aux conditions de Discord et de Disboard : a utiliser a vos risques.

# Deploy templates

Deze map bevat systeemtemplates die door `install.sh`
worden gekopieerd naar:

- /etc/systemd/system/
- /etc/nginx/sites-available/
- /etc/logrotate.d/

Wijzig deze bestanden niet direct in /etc,
maar pas ze hier aan en run daarna opnieuw:

    sudo ./install.sh

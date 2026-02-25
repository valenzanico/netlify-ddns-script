#!/usr/bin/env python3
"""
Dynamic DNS (DDNS) script for Netlify
Monitora l'IP pubblico e aggiorna automaticamente un record DNS A su Netlify
"""

import os
import sys
import json
import time
import logging
import requests
from datetime import datetime
from pathlib import Path

# ============================================================================
# CONFIGURAZIONE
# ============================================================================

# Salva configurazione in un file .env o modifica direttamente qui
NETLIFY_API_TOKEN = os.getenv("NETLIFY_API_TOKEN", "YOUR_NETLIFY_API_TOKEN")
DNS_ZONE_ID = os.getenv("DNS_ZONE_ID", "YOUR_ZONE_ID")
SUBDOMAIN = os.getenv("SUBDOMAIN", "subdomain")  # es. "ddns", "remote", etc.

# Intervallo di controllo in secondi (300 = 5 minuti)
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))

# File dove salva l'IP precedente
STATE_FILE = Path.home() / ".netlify_ddns_state.json"

# ============================================================================
# SETUP LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(Path.home() / "netlify_ddns.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# FUNZIONI UTILITY
# ============================================================================

def get_public_ip():
    """Ottiene l'IP pubblico del dispositivo"""
    try:
        # Prova con ipify
        response = requests.get("https://api.ipify.org?format=json", timeout=10)
        response.raise_for_status()
        ip = response.json()["ip"]
        logger.info(f"IP pubblico rilevato: {ip}")
        return ip
    except Exception as e:
        logger.error(f"Errore nel recupero dell'IP pubblico: {e}")
        try:
            # Fallback con icanhazip
            response = requests.get("https://icanhazip.com", timeout=10)
            response.raise_for_status()
            ip = response.text.strip()
            logger.info(f"IP pubblico rilevato (fallback): {ip}")
            return ip
        except Exception as e2:
            logger.error(f"Fallback anche fallito: {e2}")
            return None

def load_state():
    """Carica lo stato precedente (IP salvato) dal file"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                return data.get("ip"), data.get("record_id")
        except Exception as e:
            logger.error(f"Errore lettura state file: {e}")
    return None, None

def save_state(ip, record_id):
    """Salva l'IP e l'ID del record DNS nello state file"""
    try:
        data = {
            "ip": ip,
            "record_id": record_id,
            "updated_at": datetime.now().isoformat()
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Stato salvato: IP={ip}, Record ID={record_id}")
    except Exception as e:
        logger.error(f"Errore salvataggio state file: {e}")

def get_netlify_dns_records():
    """Ottiene tutti i record DNS della zona da Netlify"""
    try:
        url = f"https://api.netlify.com/api/v1/dns_zones/{DNS_ZONE_ID}/dns_records"
        headers = {
            "Authorization": f"Bearer {NETLIFY_API_TOKEN}",
            "Content-Type": "application/json"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        records = response.json()
        logger.debug(f"Record DNS ottenuti: {len(records)} record trovati")
        return records
    except requests.exceptions.HTTPError as e:
        logger.error(f"Errore HTTP: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logger.error(f"Errore nel recupero dei record DNS: {e}")
        return None

def find_dns_record(hostname):
    """Trova il record DNS A per il subdomain specificato"""
    records = get_netlify_dns_records()
    if not records:
        return None
    
    for record in records:
        if record.get("hostname") == hostname and record.get("type") == "A":
            logger.info(f"Record trovato: {hostname} -> {record.get('value')}")
            return record
    
    logger.warning(f"Nessun record A trovato per: {hostname}")
    return None

def delete_dns_record(record_id):
    """Cancella un record DNS dato il suo ID"""
    try:
        url = f"https://api.netlify.com/api/v1/dns_zones/{DNS_ZONE_ID}/dns_records/{record_id}"
        headers = {
            "Authorization": f"Bearer {NETLIFY_API_TOKEN}",
            "Content-Type": "application/json"
        }
        response = requests.delete(url, headers=headers, timeout=10)
        response.raise_for_status()
        logger.info(f"Record DNS cancellato: {record_id}")
        return True
    except requests.exceptions.HTTPError as e:
        logger.error(f"Errore HTTP nella cancellazione: {e.response.status_code} - {e.response.text}")
        return False
    except Exception as e:
        logger.error(f"Errore nella cancellazione del record DNS: {e}")
        return False

def create_dns_record(hostname, ip, ttl=3600):
    """Crea un nuovo record DNS A"""
    try:
        url = f"https://api.netlify.com/api/v1/dns_zones/{DNS_ZONE_ID}/dns_records"
        headers = {
            "Authorization": f"Bearer {NETLIFY_API_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "type": "A",
            "hostname": hostname,
            "value": ip,
            "ttl": ttl
        }
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        new_record = response.json()
        record_id = new_record.get("id")
        logger.info(f"Record DNS creato: {hostname} -> {ip} (ID: {record_id})")
        return record_id
    except requests.exceptions.HTTPError as e:
        logger.error(f"Errore HTTP nella creazione: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logger.error(f"Errore nella creazione del record DNS: {e}")
        return None

def update_dns_record(hostname, new_ip):
    """Aggiorna il record DNS A per il subdomain specificato"""
    # Trova il record esistente
    current_record = find_dns_record(hostname)
    
    if not current_record:
        logger.warning(f"Nessun record precedente trovato, creo uno nuovo...")
        new_record_id = create_dns_record(hostname, new_ip)
        if new_record_id:
            save_state(new_ip, new_record_id)
            return True
        return False
    
    record_id = current_record.get("id")
    old_ip = current_record.get("value")
    
    # Se l'IP è uguale, non fare niente
    if old_ip == new_ip:
        logger.info(f"L'IP non è cambiato ({new_ip}), nessun aggiornamento necessario")
        return True
    
    logger.info(f"IP cambiato! {old_ip} -> {new_ip}")
    
    # Cancella il vecchio record
    if not delete_dns_record(record_id):
        logger.error("Impossibile cancellare il vecchio record")
        return False
    
    # Crea il nuovo record
    new_record_id = create_dns_record(hostname, new_ip)
    if new_record_id:
        save_state(new_ip, new_record_id)
        logger.info(f"Aggiornamento DNS completato!")
        return True
    
    return False

def validate_config():
    """Valida la configurazione prima di eseguire"""
    if NETLIFY_API_TOKEN == "YOUR_NETLIFY_API_TOKEN":
        logger.error("ERRORE: NETLIFY_API_TOKEN non configurato!")
        logger.error("Imposta la variabile d'ambiente NETLIFY_API_TOKEN")
        return False

    
    if DNS_ZONE_ID == "YOUR_ZONE_ID":
        logger.error("ERRORE: DNS_ZONE_ID non configurato!")
        logger.error("Imposta la variabile d'ambiente DNS_ZONE_ID")
        return False
    
    logger.info(f"Configurazione:")
    logger.info(f"  - SITE_ID: {NETLIFY_SITE_ID}")
    logger.info(f"  - ZONE_ID: {DNS_ZONE_ID}")
    logger.info(f"  - SUBDOMAIN: {SUBDOMAIN}")
    logger.info(f"  - CHECK_INTERVAL: {CHECK_INTERVAL}s")
    
    return True

# ============================================================================
# MAIN LOOP
# ============================================================================

def main_loop():
    """Loop principale di monitoraggio"""
    if not validate_config():
        sys.exit(1)
    
    logger.info("="*60)
    logger.info("NETLIFY DYNAMIC DNS - Avvio")
    logger.info("="*60)
    
    iteration = 0
    
    try:
        while True:
            iteration += 1
            logger.info(f"\n--- Ciclo {iteration} ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---")
            
            # Ottieni IP pubblico
            current_ip = get_public_ip()
            if not current_ip:
                logger.error("Impossibile ottenere l'IP pubblico, riprovo al prossimo ciclo")
                time.sleep(CHECK_INTERVAL)
                continue
            
            # Confronta con l'IP salvato
            saved_ip, saved_record_id = load_state()
            
            if saved_ip == current_ip:
                logger.info(f"IP invariato: {current_ip}")
            else:
                logger.warning(f"IP cambiato! {saved_ip} -> {current_ip}")
                update_dns_record(SUBDOMAIN, current_ip)
            
            # Attendi prima del prossimo controllo
            logger.info(f"Prossimo controllo tra {CHECK_INTERVAL} secondi...")
            time.sleep(CHECK_INTERVAL)
    
    except KeyboardInterrupt:
        logger.info("\nInterrotto dall'utente (Ctrl+C)")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Errore inaspettato: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main_loop()

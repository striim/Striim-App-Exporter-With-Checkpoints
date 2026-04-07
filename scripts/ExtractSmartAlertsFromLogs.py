import re
import os
from datetime import datetime

class StriimLogScraper:
    def __init__(self, log_path):
        self.log_path = log_path
        # Format: 2023-10-27 10:00:00,000
        self.date_pattern = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}"

    def extract_value_after(self, text, keyword):
        if keyword in text:
            return text.split(keyword)[1].strip()
        return None

    def extract_value_before(self, text, keyword):
        if keyword in text:
            return text.split(keyword)[0].strip()
        return None

    def parse_smart_alert(self, date_str, server, app, log_level, message):
        """Replicates the Java processSmartAlert logic"""
        alert_matched = self.extract_value_after(message, "Alert Matched:")
        message_content = self.extract_value_after(message, "Message:")

        source_or_target = None
        alert_type = "Unknown"

        # Logic from LogWatcherHelper.java to find the entity
        if "Source " in message_content:
            source_or_target = self.extract_value_before(self.extract_value_after(message_content, "Source "), ":")
        elif "Target " in message_content:
            source_or_target = self.extract_value_before(self.extract_value_after(message_content, "Target "), ":")
        elif "Application " in message_content:
            source_or_target = self.extract_value_before(self.extract_value_after(message_content, "Application "), ":")
        elif "Node " in message_content:
            source_or_target = self.extract_value_before(self.extract_value_after(message_content, "Node "), ":")
        elif message_content.startswith("OJet"):
            source_or_target = "target"
            alert_type = "target"

        # Determine type based on the first word of the message content
        if ":" in message_content:
            first_part = message_content.split(":")[0].strip()
            alert_type = first_part.split(" ")[0].lower()

        return {
            "timestamp": date_str,
            "server": server,
            "app": app,
            "log_level": log_level,
            "alert_name": alert_matched.split(",")[0] if alert_matched else "N/A",
            "entity": source_or_target,
            "type": alert_type,
            "full_message": message_content
        }

    def scrape(self):
        alerts = []
        if not os.path.exists(self.log_path):
            print(f"Error: {self.log_path} not found.")
            return alerts

        with open(self.log_path, 'r') as f:
            for line in f:
                # 1. Check if line starts with a timestamp and contains @
                if re.match(self.date_pattern, line) and "@" in line:
                    try:
                        # Replicating Java parts = logEntry.split("@", 3)
                        parts = line.split("@")
                        if len(parts) < 3: continue

                        timestamp = parts[0].strip()
                        server = parts[1].strip() or "N/A"
                        
                        # Replicating streamAndRest = parts[2].split("-", 2)
                        rest = parts[2].split("-", 1)
                        if len(rest) < 2: continue
                        
                        app = rest[0].strip() or "N/A"
                        
                        # Replicating logLevelAndMessage = rest[1].trim().split(" ", 2)
                        log_parts = rest[1].strip().split(" ", 1)
                        log_level = log_parts[0].strip()
                        message = log_parts[1].strip() if len(log_parts) > 1 else ""

                        # 2. Filter for Smart Alerts: WARN level + "Alert Matched:"
                        if log_level == "WARN" and "Alert Matched:" in message:
                            # Skip System$Notification to match Java logic
                            if "Application System$Notification" in message:
                                continue
                                
                            alert_data = self.parse_smart_alert(timestamp, server, app, log_level, message)
                            alerts.append(alert_data)

                    except Exception as e:
                        continue # Skip malformed lines
        return alerts

# --- Usage ---
if __name__ == "__main__":
    # Update this to your actual log path
    LOG_FILE = "/opt/striim/logs/striim.server.log" 
    
    scraper = StriimLogScraper(LOG_FILE)
    found_alerts = scraper.scrape()

    print(f"Found {len(found_alerts)} SmartAlerts:\n")
    for a in found_alerts:
        print(f"[{a['timestamp']}] {a['alert_name']} on {a['type']} '{a['entity']}'")
        print(f"Message: {a['full_message']}\n" + "-"*50)

from google_auth_oauthlib.flow import InstalledAppFlow
import os

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

def main():
    if not os.path.exists('gmail_credentials.json'):
        print("FEHLER: gmail_credentials.json fehlt!")
        return

    flow = InstalledAppFlow.from_client_secrets_file('gmail_credentials.json', SCOPES)
    creds = flow.run_local_server(port=0)

    with open('token.json', 'w') as token:
        token.write(creds.to_json())
    
    print("ERFOLG: token.json wurde erstellt!")

if __name__ == '__main__':
    main()
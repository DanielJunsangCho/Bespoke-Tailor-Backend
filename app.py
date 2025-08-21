from flask import Flask
from flask_cors import CORS
from mcp_client.client import MCPClient

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": [
            "chrome-extension:fbcfniofgnkajmaijeogmahmbchncdbl"
        ]
    }
})


@app.route('/tailor_resume')
def tailor_resume():
    client = MCPClient()
    return client.chat_loop()




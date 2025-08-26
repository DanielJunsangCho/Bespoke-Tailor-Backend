from flask import Flask, request, jsonify
from flask_cors import CORS
from mcp_client.client import MCPConnectionPool
import atexit
import os
import time
from collections import defaultdict

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": [
            "chrome-extension:fbcfniofgnkajmaijeogmahmbchncdbl"
        ]
    }
})
# CORS(app)

# Initialize connection pool
server_path = os.path.join(os.path.dirname(__file__), "latex-mcp", "server.py")
mcp_pool = MCPConnectionPool(server_path, pool_size=3)

# Rate limiting storage
request_counts = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # 1 minute
RATE_LIMIT_MAX_REQUESTS = 10  # 10 requests per minute per IP

def check_rate_limit(client_ip):
    """Check if client IP has exceeded rate limit"""
    now = time.time()
    # Clean old requests
    request_counts[client_ip] = [req_time for req_time in request_counts[client_ip] 
                                if now - req_time < RATE_LIMIT_WINDOW]
    
    # Check if under limit
    if len(request_counts[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
        return False
    
    # Add current request
    request_counts[client_ip].append(now)
    return True

# Initialize MCP connection pool
print("Initializing MCP connection pool...")
mcp_pool.initialize_pool()

@app.route('/api/tailor_resume', methods=['POST'])
def tailor_resume():
    try:
        # Rate limiting check
        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
        if not check_rate_limit(client_ip):
            return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        resume_data = data.get('resume_data', '')
        job_description = data.get('job_description', '')
        
        if not resume_data or not job_description:
            return jsonify({"error": "Missing resume_data or job_description"}), 400
        
        # Process using connection pool
        result = mcp_pool.process_resume_request(resume_data, job_description)
        
        if result.startswith("Error:"):
            return jsonify({"error": result}), 503
        
        return jsonify({"result": result})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health_check():
    """Health check endpoint"""
    pool_status = {
        "available_connections": len(mcp_pool.available),
        "connections_in_use": len(mcp_pool.in_use),
        "pool_initialized": mcp_pool.initialized,
        "total_pool_size": mcp_pool.pool_size
    }
    
    # Check if pool is healthy (has at least 1 available connection)
    is_healthy = mcp_pool.initialized and len(mcp_pool.available) > 0
    status_code = 200 if is_healthy else 503
    
    return jsonify({
        "status": "healthy" if is_healthy else "unhealthy", 
        "mcp_pool": pool_status
    }), status_code

@app.route('/health/reconnect', methods=['POST'])
def force_reconnect():
    """Force reconnect all MCP connections - for debugging"""
    try:
        mcp_pool.cleanup_pool()
        mcp_pool.initialize_pool()
        return jsonify({"message": "MCP pool reconnected successfully"})
    except Exception as e:
        return jsonify({"error": f"Failed to reconnect: {str(e)}"}), 500

# Cleanup function
def cleanup_resources():
    """Clean up MCP pool resources"""
    try:
        mcp_pool.cleanup_pool()
        print("MCP pool cleaned up successfully")
    except Exception as e:
        print(f"Error during cleanup: {e}")

# Register cleanup on app shutdown
atexit.register(cleanup_resources)

if __name__ == '__main__':
    try:
        app.run(debug=True, port=5000)
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
        cleanup_resources()
    finally:
        cleanup_resources()

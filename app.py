"""
Main Flask application factory.
Creates and configures the Flask application.
"""
import sys
import os
from flask import Flask, render_template, send_from_directory
import logging
import atexit

# PyInstaller için path düzeltmesi
if getattr(sys, 'frozen', False):
    # PyInstaller bundle içindeyiz
    bundle_dir = sys._MEIPASS
    exe_dir = os.path.dirname(sys.executable)
    
    # Working directory'yi executable'ın bulunduğu dizine ayarla
    os.chdir(exe_dir)
    
    # Template ve static klasörlerini bundle içinden al
    template_folder = os.path.join(bundle_dir, 'frontend', 'templates')
    static_folder = os.path.join(bundle_dir, 'frontend', 'static')
    
    # Debug için path'leri yazdır ve dosyaya kaydet
    debug_info = f"""
=== NIQ Debug Info (Natural Intelligence Query) ===
Bundle dir: {bundle_dir}
Template folder: {template_folder}
Static folder: {static_folder}
Template exists: {os.path.exists(template_folder)}
Static exists: {os.path.exists(static_folder)}
Working dir: {os.getcwd()}
Exe dir: {exe_dir}
"""
    print(debug_info)
    
    # Debug bilgilerini dosyaya kaydet
    try:
        with open('debug.log', 'w', encoding='utf-8') as f:
            f.write(debug_info)
            if os.path.exists(template_folder):
                f.write(f"Template files: {os.listdir(template_folder)}\n")
            if os.path.exists(static_folder):
                f.write(f"Static files: {os.listdir(static_folder)}\n")
    except Exception as e:
        print(f"Debug log yazma hatası: {e}")
    
    # Uygulama verilerini exe dizininde sakla
    os.environ['SQL_AGENT_HOME'] = exe_dir
else:
    # Normal Python çalıştırması
    template_folder = 'frontend/templates'
    static_folder = 'frontend/static'

from backend.config.config import Config
from backend.routes.routes import api_bp, db_routes
from backend.core.utils import LoggingUtils


def create_app() -> Flask:
    """
    Application factory pattern.
    Creates and configures Flask application.
    
    Returns:
        Configured Flask application
    """
    # Validate configuration
    Config.validate_config()
    
    # Setup logging
    LoggingUtils.setup_logging()
    logger = logging.getLogger(__name__)
    
    # Create Flask app with dynamic template and static folders
    app = Flask(__name__, 
                template_folder=template_folder,
                static_folder=static_folder)
    
    # Configure app
    app.config['SECRET_KEY'] = Config.SECRET_KEY
    app.config['DEBUG'] = False  # Desktop sürümde debug kapalı
    app.config['TEMPLATES_AUTO_RELOAD'] = False
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    
    # Register blueprints
    app.register_blueprint(api_bp)
    
    
    # Register main route
    @app.route("/")
    def index():
        """Serve main application page."""
        return render_template("index.html")
    
    # Setup Guide route
    @app.route("/kurulum-rehberi")
    def setup_guide():
        """Serve setup guide placeholder page."""
        return render_template("setup_guide.html")
    
    # Image serving route for setup guide images
    @app.route("/static/images/<path:filename>")
    def serve_images(filename):
        """Serve images from static/images directory with proper encoding."""
        try:
            # Get the correct static folder path
            if getattr(sys, 'frozen', False):
                images_folder = os.path.join(bundle_dir, 'frontend', 'static', 'images')
            else:
                images_folder = os.path.join('frontend', 'static', 'images')
            return send_from_directory(images_folder, filename)
        except Exception as e:
            logger.error(f"Error serving image {filename}: {e}")
            return {"error": "Image not found"}, 404
    
    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        """Handle 404 errors."""
        return {"error": "Endpoint not found"}, 404
    
    @app.errorhandler(500)
    def internal_error(error):
        """Handle 500 errors."""
        logger.error(f"Internal server error: {error}")
        return {"error": "Internal server error"}, 500
    
    logger.info("Flask application created successfully")
    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    app.run(debug=False, host='127.0.0.1', port=5000)

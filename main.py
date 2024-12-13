import os
import uuid
from datetime import datetime, timedelta
from flask import Flask, render_template, request, send_from_directory, jsonify
from flask_socketio import SocketIO, emit
from PIL import Image, ImageOps
import threading
import time

# Configuration
UPLOAD_FOLDER = 'uploads'
COLLAGE_FOLDER = 'collages'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
A4_SIZE = (2480, 3508)  # Pixels at 300 DPI
COLLAGE_INTERVAL = 60  # Seconds between collages

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'  # Change this to a random secret key
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['COLLAGE_FOLDER'] = COLLAGE_FOLDER

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")

# Ensure upload and collage directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(COLLAGE_FOLDER, exist_ok=True)

# Store uploaded images with timestamps
uploaded_images = []
last_collage_time = datetime.now()
recent_collage = None


def allowed_file(filename):
    """Check if file has an allowed extension."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def create_dynamic_collage():
    """
    Create a collage that dynamically fills the entire canvas based on uploaded images.
    If no images are uploaded, create a blank collage.
    """
    global last_collage_time, recent_collage

    # Filter images from last 60 seconds
    current_time = datetime.now()
    recent_images = [
        img for img in uploaded_images
        if current_time - img['timestamp'] <= timedelta(seconds=COLLAGE_INTERVAL)
    ]

    # Create a blank A4 white background
    collage = Image.new('RGB', A4_SIZE, color='white')

    if not recent_images:
        # Save blank collage if no images are uploaded
        collage_filename = f"collage_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        collage_path = os.path.join(app.config['COLLAGE_FOLDER'], collage_filename)
        collage.save(collage_path)

        # Update last collage time and recent collage
        last_collage_time = current_time
        recent_collage = collage_filename

        # Broadcast the new blank collage to all connected clients
        socketio.emit('new_collage', {
            'filename': collage_filename,
            'uploaded_images': []
        })

        return collage_filename

    # Determine layout based on number and aspect ratios of images
    num_images = len(recent_images)

    if num_images == 1:
        # Single image - fill entire canvas
        img = Image.open(recent_images[0]['path'])
        img = ImageOps.fit(img, A4_SIZE, Image.LANCZOS)
        collage.paste(img, (0, 0))
    elif num_images == 2:
        # Two images - split vertically
        width = A4_SIZE[0]
        half_width = width // 2

        # First image
        img1 = Image.open(recent_images[0]['path'])
        img1 = ImageOps.fit(img1, (half_width, A4_SIZE[1]), Image.LANCZOS)
        collage.paste(img1, (0, 0))

        # Second image
        img2 = Image.open(recent_images[1]['path'])
        img2 = ImageOps.fit(img2, (width - half_width, A4_SIZE[1]), Image.LANCZOS)
        collage.paste(img2, (half_width, 0))
    elif num_images == 3:
        # Three images - two on top, one on bottom
        width = A4_SIZE[0]
        height = A4_SIZE[1]

        # Top two images
        top_height = height * 2 // 3
        top_width = width // 2

        img1 = Image.open(recent_images[0]['path'])
        img1 = ImageOps.fit(img1, (top_width, top_height), Image.LANCZOS)
        collage.paste(img1, (0, 0))

        img2 = Image.open(recent_images[1]['path'])
        img2 = ImageOps.fit(img2, (width - top_width, top_height), Image.LANCZOS)
        collage.paste(img2, (top_width, 0))

        # Bottom image
        img3 = Image.open(recent_images[2]['path'])
        img3 = ImageOps.fit(img3, (width, height - top_height), Image.LANCZOS)
        collage.paste(img3, (0, top_height))
    else:
        # 4 or more images - grid layout
        width = A4_SIZE[0]
        height = A4_SIZE[1]

        import numpy as np
        grid_size = int(np.ceil(np.sqrt(num_images)))
        cell_width = width // grid_size
        cell_height = height // grid_size

        for i, img_data in enumerate(recent_images):
            row = i // grid_size
            col = i % grid_size

            img = Image.open(img_data['path'])
            img = ImageOps.fit(img, (cell_width, cell_height), Image.LANCZOS)

            x = col * cell_width
            y = row * cell_height

            collage.paste(img, (x, y))

    # Save collage
    collage_filename = f"collage_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    collage_path = os.path.join(app.config['COLLAGE_FOLDER'], collage_filename)
    collage.save(collage_path)

    # Update last collage time and recent collage
    last_collage_time = current_time
    recent_collage = collage_filename

    # Broadcast the new collage to all connected clients
    socketio.emit('new_collage', {
        'filename': collage_filename,
        'uploaded_images': [os.path.basename(img['path']) for img in uploaded_images]
    })

    return collage_filename



def collage_scheduler():
    """Periodically create collages and clean up uploaded images."""
    global uploaded_images, last_collage_time

    while True:
        time.sleep(COLLAGE_INTERVAL)

        # Create collage
        create_dynamic_collage()

        # Clean up uploaded images older than interval
        current_time = datetime.now()
        uploaded_images = [
            img for img in uploaded_images
            if current_time - img['timestamp'] <= timedelta(seconds=COLLAGE_INTERVAL)
        ]


@app.route('/')
def index():
    """Render the main page."""
    # Get list of uploaded image filenames
    uploaded_filenames = [os.path.basename(img['path']) for img in uploaded_images]

    # Get list of recent collages
    collage_files = sorted(os.listdir(app.config['COLLAGE_FOLDER']), reverse=True)

    return render_template('index.html',
                           uploaded_images=uploaded_filenames,
                           collages=collage_files,
                           collage_interval=COLLAGE_INTERVAL)


@app.route('/upload', methods=['POST'])
def upload_file():
    # Check if the post request has the file part
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']

    # If user does not select file, browser also submit an empty part without filename
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file and allowed_file(file.filename):
        # Generate unique filename
        filename = str(uuid.uuid4()) + os.path.splitext(file.filename)[1]
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Store image with timestamp
        uploaded_images.append({
            'path': filepath,
            'timestamp': datetime.now()
        })

        # Broadcast the new upload to all connected clients
        socketio.emit('new_upload', {
            'filename': filename,
            'uploaded_images': [os.path.basename(img['path']) for img in uploaded_images]
        })

        return jsonify({'message': 'File uploaded successfully', 'filename': filename}), 200

    return jsonify({'error': 'File type not allowed'}), 400


@socketio.on('connect')
def handle_connect():
    """Send current state to newly connected clients"""
    emit('initial_state', {
        'uploaded_images': [os.path.basename(img['path']) for img in uploaded_images],
        'recent_collage': recent_collage,
        'remaining_time': int(max(0, COLLAGE_INTERVAL - (datetime.now() - last_collage_time).total_seconds()))
    })


@app.route('/uploads/<filename>')
def serve_upload(filename):
    """Serve uploaded images."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/collages/<filename>')
def serve_collage(filename):
    """Serve saved collage images."""
    return send_from_directory(app.config['COLLAGE_FOLDER'], filename)


def start_scheduler():
    """Start the collage scheduler in a separate thread."""
    scheduler_thread = threading.Thread(target=collage_scheduler, daemon=True)
    scheduler_thread.start()


if __name__ == '__main__':
    # Start the scheduler before running the app
    start_scheduler()

    # Run the SocketIO app
    socketio.run(app, debug=True)
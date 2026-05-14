import os
from dotenv import load_dotenv

load_dotenv()

import cloudinary
import cloudinary.uploader
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
from bson.objectid import ObjectId
from werkzeug.utils import secure_filename
from PIL import Image
from datetime import datetime, time
import math
import re
from timezonefinder import TimezoneFinder
import pytz

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

app = Flask(__name__, static_folder="static", static_url_path="/static")

UPLOAD_FOLDER = "/tmp"

app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-cicipin-2024')

def process_image(path, size=(600,400)):
    try:
        img = Image.open(path)
        img = img.convert('RGB')
        img.thumbnail((size[0], size[1]), Image.ANTIALIAS)

        new_img = Image.new('RGB', size, (255,255,255))
        x = (size[0] - img.width) // 2
        y = (size[1] - img.height) // 2
        new_img.paste(img, (x, y))
        new_img.save(path)

    except Exception as e:
        app.logger.warning("failed to process image %s: %s", path, e)

try:
    client = MongoClient(os.environ.get("MONGODB_URI"), serverSelectionTimeoutMS=5000)
    db = client[os.environ.get("DB_NAME")]
    client.admin.command('ping')
    restaurants_collection = db["restaurants"]

except Exception as exc:
    import logging
    logging.getLogger(__name__).error("Database connection failed: %s", exc)
    client = None
    db = None
    restaurants_collection = None

@app.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':

        username = request.form['username']
        password = request.form['password']

        if db is None:
            flash('Cannot log in: database unreachable', 'danger')
            return render_template('login.html')

        user = db.users.find_one({
            "username": username
        })

        if user and check_password_hash(user['password'], password):
            session['user_id'] = str(user['_id'])
            session['username'] = user.get('username')

            flash('You have successfully logged in', 'success')
            return redirect(url_for('index'))

        else:
            flash('Invalid username or password', 'danger')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():

    if "user_id" in session:
        return redirect(url_for('index'))

    if request.method == 'POST':

        if db is None:
            flash('Cannot register: database unreachable', 'danger')
            return redirect(url_for('register'))

        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        username = request.form['username'].strip()
        password = request.form['password']

        hashed_password = generate_password_hash(password)

        existing_user = db.users.find_one({
            "$or": [
                {"username": username},
                {"email": email}
            ]
        })

        if existing_user:
            if existing_user.get('username') == username:
                flash("Username already exists", "danger")
            else:
                flash("Email already registered", "danger")
            return redirect(url_for('register'))

        db.users.insert_one({
            "full_name": full_name,
            "email": email,
            "username": username,
            "password": hashed_password
        })

        flash("Account created successfully. Please log in.", "success")
        return redirect(url_for('login'))

    return render_template('register.html')

def is_admin():
    return session.get("username") == "admin"

def compute_average_rating(restaurant):

    reviews = restaurant.get('reviews', [])

    if reviews:
        try:
            avg = sum(r.get('rating', 0) for r in reviews) / len(reviews)
        except Exception:
            avg = 0

        restaurant['average_rating'] = round(avg, 1)

    else:
        restaurant['average_rating'] = None

    return restaurant

def compute_open_status(restaurant):

    opening_hours = restaurant.get("opening_hours")

    if not opening_hours:
        restaurant["is_open"] = None
        return restaurant

    try:
        latitude = restaurant.get('latitude')
        longitude = restaurant.get('longitude')
        
        timezone_str = "Asia/Jakarta" 
        
        if latitude and longitude:
            try:
                tf = TimezoneFinder()
                timezone_str = tf.timezone_at(lat=float(latitude), lng=float(longitude))
                if not timezone_str:
                    timezone_str = "Asia/Jakarta"
            except Exception:
                timezone_str = "Asia/Jakarta"
        
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz).time()

        match = re.search(r'(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})', opening_hours)
        if not match:
            restaurant["is_open"] = None
            return restaurant

        open_hour, open_min, close_hour, close_min = map(int, match.groups())

        open_time = time(open_hour, open_min)
        close_time = time(close_hour, close_min)

        if close_time < open_time:
            is_open = now >= open_time or now < close_time
        else:
            is_open = open_time <= now <= close_time
        
        restaurant["is_open"] = is_open

    except Exception as e:
        restaurant["is_open"] = None

    return restaurant

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def search_restaurants(search_term=None, min_rating=None, max_price=None, sort_by=None, user_lat=None, user_lon=None):

    if db is None:
        return []

    query = {}

    if search_term and search_term.lower() != "semua":
        regex = re.compile(re.escape(search_term), re.IGNORECASE)
        query = {
            "$or": [
                {"name": regex},
                {"category": regex},
                {"address": regex}
            ]
        }

    restaurants = db.restaurants.find(query)

    result = []

    for restaurant in restaurants:

        compute_average_rating(restaurant)
        compute_open_status(restaurant)

        restaurant['review_count'] = len(restaurant.get('reviews', []))

        if user_lat and user_lon and restaurant.get('latitude') and restaurant.get('longitude'):
            restaurant['distance'] = haversine(float(user_lat), float(user_lon), float(restaurant['latitude']), float(restaurant['longitude']))
            restaurant['distance_str'] = f"{restaurant['distance']:.1f} km"
        else:
            restaurant['distance'] = float('inf')
            restaurant['distance_str'] = ""

        if min_rating is not None:
            if restaurant['average_rating'] is None or restaurant['average_rating'] < min_rating:
                continue

        result.append(restaurant)

    if sort_by == 'rating':
        result.sort(key=lambda x: x.get('average_rating') or 0, reverse=True)
    elif sort_by == 'terlaris':
        result.sort(key=lambda x: x.get('review_count') or 0, reverse=True)
    elif sort_by == 'jarak' and user_lat and user_lon:
        result.sort(key=lambda x: x.get('distance'))

    return result


@app.route('/')
def index():

    if "user_id" not in session:
        return redirect(url_for('login'))

    category = request.args.get("category")
    min_rating = request.args.get("min_rating")
    max_price = request.args.get("max_price")
    sort_by = request.args.get("sort_by")
    user_lat = request.args.get("user_lat")
    user_lon = request.args.get("user_lon")

    min_rating = float(min_rating) if min_rating else None
    max_price = float(max_price) if max_price else None

    restaurants = search_restaurants(category, min_rating, max_price, sort_by, user_lat, user_lon)

    saved_restaurant_ids = []
    username = None
    is_authenticated = False

    restaurant_count = 0
    total_reviews = 0
    city_count = 0

    if db is not None:
        try:
            # 1. Total Restoran
            restaurant_count = db.restaurants.count_documents({})

            # 2. Total Ulasan (Data Asli dari array reviews)
            reviews_agg = list(db.restaurants.aggregate([
                {"$project": {"count": {"$size": {"$ifNull": ["$reviews", []]}}}},
                {"$group": {"_id": None, "total": {"$sum": "$count"}}}
            ]))
            total_reviews = reviews_agg[0]["total"] if reviews_agg else 0

            # 3. Hitung Kota Unik (Logika Pintar: Gabung Kota, Kab, City)
            all_restaurants = db.restaurants.find({}, {"address": 1})
            city_set = set()
            
            for res in all_restaurants:
                address = res.get("address", "")
                if address:
                    # Cari nama kota/kabupaten menggunakan Regex
                    match = re.search(r'(?i)\b(?:kota|kabupaten|kab\.?)\s+([a-z\s]+)|([a-z\s]+)\s+city', address)
                    if match:
                        raw_city = match.group(1) or match.group(2)
                        city_set.add(raw_city.strip().lower())
                    else:
                        # Fallback: Ambil kata terakhir/sebelum koma terakhir
                        parts = address.split(',')
                        if parts:
                            city_set.add(parts[-1].strip().lower())
            
            city_count = len(city_set)

        except Exception as exc:
            app.logger.warning("Failed to compute dashboard stats: %s", exc)


    if "user_id" in session:
        is_authenticated = True
        username = session.get("username")
        if db is not None:
            try:
                user_wishlist = list(db.wishlists.find({"user_id": session["user_id"]}))
                saved_restaurant_ids = [str(w["restaurant_id"]) for w in user_wishlist]
            except Exception as exc:
                app.logger.warning("Failed to load wishlist: %s", exc)

    return render_template(
        'index.html',
        restaurants=restaurants,
        username=username,
        saved_restaurant_ids=saved_restaurant_ids,
        sort_by=sort_by,
        is_authenticated=is_authenticated,
        is_admin=is_admin(),
        dashboard_stats={
            'restaurant_count': restaurant_count,
            'total_reviews': total_reviews,
            'city_count': city_count
        }
    )


@app.route('/add_restaurant', methods=['GET', 'POST'])
def add_restaurant():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if not is_admin():
        flash("Permission denied", "danger")
        return redirect(url_for("index"))

    if request.method == 'POST':
        try:
            name = request.form.get('name')
            category = request.form.get('category')
            address = request.form.get('address')

            latitude = float(request.form.get('latitude'))
            longitude = float(request.form.get('longitude'))

            opening_hours = request.form.get('opening_hours', '').strip() or None
            price_range = request.form.get('price_range')

            image_url = None
            image = request.files.get("image")

            if image and image.filename != "":
                try:
                    upload_result = cloudinary.uploader.upload(image)
                    image_url = upload_result["secure_url"]
                    print("UPLOAD SUCCESS:", image_url)
                except Exception as e:
                    print("CLOUDINARY ERROR:", e)

            new_restaurant = {
                "name": name,
                "category": category,
                "address": address,
                "latitude": latitude,
                "longitude": longitude,
                "opening_hours": opening_hours,
                "price_range": price_range,
                "image_url": image_url,
                "reviews": []
            }

            db.restaurants.insert_one(new_restaurant)

            flash("Restaurant added successfully!", "success")
            return redirect(url_for('index'))

        except Exception as e:
            print("ADD RESTAURANT ERROR:", e)
            flash("Failed to add restaurant", "danger")
            return redirect(url_for('add_restaurant'))

    return render_template('add_restaurant.html')


@app.route('/edit_restaurant/<restaurant_id>', methods=['GET', 'POST'])
def edit_restaurant(restaurant_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if not is_admin():
        flash("Permission denied", "danger")
        return redirect(url_for("index"))

    restaurant = db.restaurants.find_one({"_id": ObjectId(restaurant_id)})

    if request.method == 'POST':

        name = request.form['name']
        category = request.form['category']
        address = request.form['address']
        opening_hours = request.form.get('opening_hours', '').strip() or None
        latitude = float(request.form['latitude']) if request.form.get('latitude') else None
        longitude = float(request.form['longitude']) if request.form.get('longitude') else None
        price_range = request.form['price_range']

        update_fields = {
            "name": name,
            "category": category,
            "address": address,
            "opening_hours": opening_hours,
            "latitude": latitude,
            "longitude": longitude,
            "price_range": price_range
        }

        image = request.files.get("image")
        if image and image.filename != "":
            try:
                upload_result = cloudinary.uploader.upload(image)
                update_fields["image_url"] = upload_result["secure_url"]
            except Exception as e:
                app.logger.warning("CLOUDINARY ERROR DURING EDIT: %s", e)

        db.restaurants.update_one(
            {"_id": ObjectId(restaurant_id)},
            {"$set": update_fields}
        )

        flash("Restaurant updated successfully!", "success")
        return redirect(url_for('index'))

    return render_template('edit_restaurant.html', restaurant=restaurant)

@app.route('/delete_restaurant/<restaurant_id>')
def delete_restaurant(restaurant_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if not is_admin():
        flash("Permission denied", "danger")
        return redirect(url_for("index"))

    db.restaurants.delete_one({"_id": ObjectId(restaurant_id)})

    flash("Restaurant deleted successfully!", "success")

    return redirect(url_for('index'))


@app.route('/add_review/<restaurant_id>', methods=['GET', 'POST'])
def add_review(restaurant_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    restaurant = db.restaurants.find_one({"_id": ObjectId(restaurant_id)})

    if not restaurant:
        flash('Restaurant not found', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':

        rating = float(request.form.get('rating', 0))
        comment = request.form['comment']

        review_image = None

        if 'image' in request.files:
            image = request.files['image']

            if image and image.filename:
                try:
                    upload_result = cloudinary.uploader.upload(
                        image,
                        resource_type="image"
                    )
                    review_image = upload_result["secure_url"]
                except Exception as e:
                    app.logger.error(f"Cloudinary review upload failed: {e}")

        db.restaurants.update_one(
            {"_id": ObjectId(restaurant_id)},
            {
                "$push": {
                    "reviews": {
                        "user_id": session["user_id"],
                        "username": session["username"],
                        "rating": rating,
                        "comment": comment,
                        "image_url": review_image,
                        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
                    }
                }
            }
        )

        flash("Review added successfully!", "success")

        return redirect(url_for('restaurant_detail', restaurant_id=restaurant_id))

    return render_template('add_review.html', restaurant=restaurant)


@app.route('/restaurant/<restaurant_id>')
def restaurant_detail(restaurant_id):

    if restaurants_collection is None:
        flash('Database connection failed', 'danger')
        return redirect(url_for('index'))

    restaurant = restaurants_collection.find_one({
        "_id": ObjectId(restaurant_id)
    })

    if restaurant:

        compute_average_rating(restaurant)
        compute_open_status(restaurant)

        rating_counts = {stars: 0 for stars in range(1, 6)}
        total_reviews = len(restaurant.get('reviews', []))
        for review in restaurant.get('reviews', []):
            try:
                star_value = int(review.get('rating', 0))
            except (TypeError, ValueError):
                star_value = 0
            if 1 <= star_value <= 5:
                rating_counts[star_value] += 1

        rating_percentages = {}
        for star in range(1, 6):
            if total_reviews > 0:
                rating_percentages[star] = round((rating_counts[star] / total_reviews * 100), 1)
            else:
                rating_percentages[star] = 0

        return render_template(
            'restaurant_detail.html',
            restaurant=restaurant,
            rating_counts=rating_counts,
            rating_percentages=rating_percentages,
            total_reviews=total_reviews,
            username=session.get('username'),
            is_authenticated="user_id" in session
        )

    flash('Restaurant not found', 'error')
    return redirect(url_for('index'))


@app.route('/logout')
def logout():

    session.clear()

    flash('You have successfully logged out.', 'success')

    return redirect(url_for('login'))

@app.route('/toggle_wishlist', methods=['POST'])
def toggle_wishlist():
    if "user_id" not in session:
        return {"success": False, "message": "Unauthorized"}, 401

    data = request.json
    restaurant_id = data.get("restaurant_id")

    existing = db.wishlists.find_one({"user_id": session["user_id"], "restaurant_id": ObjectId(restaurant_id)})

    if existing:
        db.wishlists.delete_one({"_id": existing["_id"]})
        is_saved = False
    else:
        db.wishlists.insert_one({"user_id": session["user_id"], "restaurant_id": ObjectId(restaurant_id)})
        is_saved = True

    return {"success": True, "is_saved": is_saved}

@app.route('/wishlist')
def wishlist():
    if "user_id" not in session:
        return redirect(url_for("login"))

    wishlist_docs = list(db.wishlists.find({"user_id": session["user_id"]}))
    restaurant_ids = [w["restaurant_id"] for w in wishlist_docs]

    restaurants = list(db.restaurants.find({"_id": {"$in": restaurant_ids}}))

    for r in restaurants:
        compute_average_rating(r)
        compute_open_status(r)

    saved_restaurant_ids = [str(i) for i in restaurant_ids]

    return render_template('wishlist.html', restaurants=restaurants, username=session["username"], saved_restaurant_ids=saved_restaurant_ids)

if __name__ == '__main__':
    app.run(debug=True)
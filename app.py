from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
import json

app = Flask(__name__)
app.secret_key = "change_this_to_some_secret_in_prod"

DB = "crop_advisory.db"

# ----------------- DB helpers -----------------
def get_conn():
    return sqlite3.connect(DB)

def init_db():
    conn = get_conn()
    c = conn.cursor()

    # users
    c.execute("""CREATE TABLE IF NOT EXISTS users (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 username TEXT UNIQUE,
                 password_hash TEXT,
                 region TEXT,
                 points INTEGER DEFAULT 0
                 )""")

    # posts
    c.execute("""CREATE TABLE IF NOT EXISTS posts (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER,
                 title TEXT,
                 crop TEXT,
                 content TEXT,
                 created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY(user_id) REFERENCES users(id)
                 )""")

    # answers/comments
    c.execute("""CREATE TABLE IF NOT EXISTS answers (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 post_id INTEGER,
                 user_id INTEGER,
                 content TEXT,
                 created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY(post_id) REFERENCES posts(id),
                 FOREIGN KEY(user_id) REFERENCES users(id)
                 )""")

    # point transactions
    c.execute("""CREATE TABLE IF NOT EXISTS point_transactions (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER,
                 points INTEGER,
                 reason TEXT,
                 created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                 )""")

    # advice logs (rule-based requests)
    c.execute("""CREATE TABLE IF NOT EXISTS advice_logs (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER,
                 request_json TEXT,
                 advice_text TEXT,
                 created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                 )""")

    # admin tables: weather, crop_care, prices, schemes
    c.execute("""CREATE TABLE IF NOT EXISTS weather (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 location TEXT,
                 forecast TEXT,
                 created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                 )""")
    c.execute("""CREATE TABLE IF NOT EXISTS crop_care (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 crop_name TEXT,
                 technique TEXT,
                 created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                 )""")
    c.execute("""CREATE TABLE IF NOT EXISTS prices (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 crop_name TEXT,
                 price REAL,
                 created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                 )""")
    c.execute("""CREATE TABLE IF NOT EXISTS schemes (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 scheme_name TEXT,
                 details TEXT,
                 created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                 )""")

    conn.commit()
    conn.close()

init_db()

# ----------------- Auth helper -----------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, username, region, points FROM users WHERE id=?", (uid,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "username": row[1], "region": row[2], "points": row[3]}
    return None

# ----------------- Routes -----------------

@app.route("/")
def index():
    user = current_user()
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT p.id, p.title, p.crop, p.content, p.created_at, u.username FROM posts p LEFT JOIN users u ON p.user_id = u.id ORDER BY p.created_at DESC LIMIT 10")
    posts = c.fetchall()
    conn.close()
    return render_template("index.html", posts=posts, user=user)

# -------- Auth --------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username").strip()
        password = request.form.get("password")
        region = request.form.get("region","")
        if not username or not password:
            flash("Fill all fields")
            return redirect(url_for("register"))
        ph = generate_password_hash(password)
        try:
            conn = get_conn()
            c = conn.cursor()
            c.execute("INSERT INTO users (username, password_hash, region) VALUES (?, ?, ?)", (username, ph, region))
            conn.commit()
            conn.close()
            flash("Registration successful. Please login.")
            return redirect(url_for("login"))
        except Exception as e:
            flash("Username probably taken.")
            return redirect(url_for("register"))
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username").strip()
        password = request.form.get("password")
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT id, password_hash FROM users WHERE username=?", (username,))
        row = c.fetchone()
        conn.close()
        if row and check_password_hash(row[1], password):
            session["user_id"] = row[0]
            flash("Logged in")
            return redirect(url_for("index"))
        flash("Invalid credentials")
        return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("Logged out")
    return redirect(url_for("index"))

# -------- Posts --------
@app.route("/add_post", methods=["GET","POST"])
def add_post():
    user = current_user()
    if request.method == "POST":
        title = request.form.get("title","").strip()
        crop = request.form.get("crop","").strip()
        content = request.form.get("content","").strip()
        if not title or not content:
            flash("Title and content required")
            return redirect(url_for("add_post"))
        uid = user["id"] if user else None
        conn = get_conn()
        c = conn.cursor()
        c.execute("INSERT INTO posts (user_id, title, crop, content) VALUES (?, ?, ?, ?)", (uid, title, crop, content))
        if uid:
            c.execute("UPDATE users SET points = points + 5 WHERE id=?", (uid,))
            c.execute("INSERT INTO point_transactions (user_id, points, reason) VALUES (?, ?, ?)", (uid, 5, "Posted question"))
        conn.commit()
        conn.close()
        flash("Post added")
        return redirect(url_for("posts_page"))
    return render_template("add_post.html", user=user)

@app.route("/posts")
def posts_page():
    user = current_user()
    q = request.args.get("q","").strip()
    crop_filter = request.args.get("crop","").strip()
    conn = get_conn()
    c = conn.cursor()
    sql = "SELECT p.id, p.title, p.crop, p.content, p.created_at, u.username FROM posts p LEFT JOIN users u ON p.user_id=u.id"
    params = []
    where = []
    if q:
        where.append("(p.title LIKE ? OR p.content LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if crop_filter:
        where.append("p.crop LIKE ?")
        params.append(f"%{crop_filter}%")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY p.created_at DESC"
    c.execute(sql, params)
    posts = c.fetchall()
    conn.close()
    return render_template("posts.html", posts=posts, user=user, q=q, crop_filter=crop_filter)

@app.route("/post/<int:post_id>", methods=["GET","POST"])
def post_detail(post_id):
    user = current_user()
    conn = get_conn()
    c = conn.cursor()
    if request.method == "POST":
        content = request.form.get("answer","").strip()
        if content:
            uid = user["id"] if user else None
            c.execute("INSERT INTO answers (post_id, user_id, content) VALUES (?, ?, ?)", (post_id, uid, content))
            if uid:
                c.execute("UPDATE users SET points = points + 3 WHERE id=?", (uid,))
                c.execute("INSERT INTO point_transactions (user_id, points, reason) VALUES (?, ?, ?)", (uid, 3, "Answered question"))
            conn.commit()
            flash("Answer posted")
            return redirect(url_for("post_detail", post_id=post_id))
    c.execute("SELECT p.id, p.title, p.crop, p.content, p.created_at, u.username, p.user_id FROM posts p LEFT JOIN users u ON p.user_id=u.id WHERE p.id=?", (post_id,))
    post = c.fetchone()
    c.execute("SELECT a.id, a.content, a.created_at, u.username FROM answers a LEFT JOIN users u ON a.user_id=u.id WHERE a.post_id=? ORDER BY a.created_at ASC", (post_id,))
    answers = c.fetchall()
    conn.close()
    return render_template("post_detail.html", post=post, answers=answers, user=user)

@app.route("/edit_post/<int:post_id>", methods=["GET","POST"])
def edit_post(post_id):
    user = current_user()
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, user_id, title, crop, content FROM posts WHERE id=?", (post_id,))
    p = c.fetchone()
    if not p:
        conn.close()
        flash("Post not found")
        return redirect(url_for("posts_page"))
    if request.method == "POST":
        title = request.form.get("title","").strip()
        crop = request.form.get("crop","").strip()
        content = request.form.get("content","").strip()
        c.execute("UPDATE posts SET title=?, crop=?, content=? WHERE id=?", (title, crop, content, post_id))
        conn.commit()
        conn.close()
        flash("Post updated")
        return redirect(url_for("post_detail", post_id=post_id))
    conn.close()
    return render_template("add_post.html", edit=True, post=p, user=user)

@app.route("/delete_post/<int:post_id>", methods=["POST"])
def delete_post(post_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM answers WHERE post_id=?", (post_id,))
    c.execute("DELETE FROM posts WHERE id=?", (post_id,))
    conn.commit()
    conn.close()
    flash("Post deleted")
    return redirect(url_for("posts_page"))

# -------- Admin forms (weather, crop_care, prices, schemes) --------
@app.route("/admin_forms", methods=["GET","POST"])
def admin_forms():
    user = current_user()
    if request.method == "POST":
        typ = request.form.get("type")
        conn = get_conn()
        c = conn.cursor()
        if typ == "weather":
            loc = request.form.get("location"); fc = request.form.get("forecast")
            c.execute("INSERT INTO weather (location, forecast) VALUES (?, ?)", (loc, fc))
        if typ == "crop_care":
            crop = request.form.get("crop_name"); tech = request.form.get("technique")
            c.execute("INSERT INTO crop_care (crop_name, technique) VALUES (?, ?)", (crop, tech))
        if typ == "prices":
            crop = request.form.get("price_crop"); price = float(request.form.get("price_val") or 0)
            c.execute("INSERT INTO prices (crop_name, price) VALUES (?, ?)", (crop, price))
        if typ == "schemes":
            name = request.form.get("scheme_name"); det = request.form.get("details")
            c.execute("INSERT INTO schemes (scheme_name, details) VALUES (?, ?)", (name, det))
        conn.commit()
        conn.close()
        flash("Saved")
        return redirect(url_for("admin_forms"))
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM weather ORDER BY created_at DESC LIMIT 5"); weather = c.fetchall()
    c.execute("SELECT * FROM crop_care ORDER BY created_at DESC LIMIT 5"); care = c.fetchall()
    c.execute("SELECT * FROM prices ORDER BY created_at DESC LIMIT 5"); prices = c.fetchall()
    c.execute("SELECT * FROM schemes ORDER BY created_at DESC LIMIT 5"); schemes = c.fetchall()
    conn.close()
    return render_template("admin_forms.html", weather=weather, care=care, prices=prices, schemes=schemes, user=user)

# -------- Advice (rule-based) --------
@app.route("/advice", methods=["GET","POST"])
def advice():
    user = current_user()
    if request.method == "POST":
        crop = request.form.get("crop","")
        try:
            temp = float(request.form.get("temp") or 0)
            hum = float(request.form.get("hum") or 0)
            ph = float(request.form.get("ph") or 7)
            price = float(request.form.get("price") or 0)
        except:
            flash("Invalid numeric input")
            return redirect(url_for("advice"))

        recs = []
        if ph < 5.5:
            recs.append("Soil acidic — consider liming (apply agricultural lime).")
        elif ph > 7.8:
            recs.append("Soil alkaline — add organic matter or sulfur carefully.")
        else:
            recs.append("Soil pH okay.")

        if temp > 35:
            recs.append("High temperature — increase irrigation in mornings/evenings.")
        if hum > 80:
            recs.append("High humidity — risk of fungal diseases; improve aeration/consider fungicide.")

        if price < 100:
            recs.append("Market price low — consider storage, processing, or alternate markets.")
        else:
            recs.append("Market price looks reasonable.")

        if "rice" in crop.lower():
            recs.append("Rice: maintain water level; check for blast & sheath blight.")

        advice_text = " ".join(recs)

        conn = get_conn()
        c = conn.cursor()
        request_json = json.dumps({"crop":crop,"temp":temp,"hum":hum,"ph":ph,"price":price})
        uid = user["id"] if user else None
        c.execute("INSERT INTO advice_logs (user_id, request_json, advice_text) VALUES (?, ?, ?)", (uid, request_json, advice_text))
        conn.commit()
        conn.close()

        return render_template("advice_result.html", advice=recs, user=user)
    return render_template("advice_result.html", advice=None, user=user)

@app.route("/my_points")
def my_points():
    user = current_user()
    if not user:
        flash("Please login")
        return redirect(url_for("login"))
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT points FROM users WHERE id=?", (user["id"],))
    r = c.fetchone()
    c.execute("SELECT points, reason, created_at FROM point_transactions WHERE user_id=? ORDER BY created_at DESC", (user["id"],))
    trans = c.fetchall()
    conn.close()
    return render_template("points.html", points=r[0] if r else 0, trans=trans, user=user)

@app.route("/api/get_posts", methods=["GET"])
def api_get_posts():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, title, crop, content, created_at FROM posts ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    data = [{"id":r[0],"title":r[1],"crop":r[2],"content":r[3],"created_at":r[4]} for r in rows]
    return jsonify(data)

if __name__ == "__main__":
    app.run(debug=True)

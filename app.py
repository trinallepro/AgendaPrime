from flask import Flask, request, redirect, url_for, render_template, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, scoped_session
import requests
from icalendar import Calendar
from datetime import datetime, timezone, timedelta
import traceback
import json
import os

# Configuration
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost/agendaprime')
SECRET_KEY = os.getenv('SECRET_KEY', 'change-this-secret-in-production')
FETCH_TIMEOUT = 10

# Initialisation Flask
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY

# Configuration pour PostgreSQL - Suppression de check_same_thread (spécifique à SQLite)
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # Vérifie la connexion avant utilisation
    pool_size=10,  # Nombre de connexions dans le pool
    max_overflow=20  # Connexions supplémentaires si nécessaire
)

Base = declarative_base()
SessionLocal = scoped_session(sessionmaker(bind=engine))

# Modèles SQLAlchemy
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(200), unique=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    icals = relationship('ICalSource', back_populates='owner')


class Friendship(Base):
    __tablename__ = 'friendships'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    friend_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    status = Column(String(20), default='pending')  # 'pending', 'accepted', 'rejected'
    created_at = Column(DateTime, default=datetime.now)

    # Évite les doublons (ex : (1, 2) et (2, 1))
    __table_args__ = (
        UniqueConstraint('user_id', 'friend_id', name='_user_friend_uc'),
    )

    user = relationship('User', foreign_keys=[user_id], backref='sent_friendships')
    friend = relationship('User', foreign_keys=[friend_id], backref='received_friendships')


class ICalSource(Base):
    __tablename__ = 'ical_sources'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    url = Column(Text, nullable=False)
    label = Column(String(200), default='Mon emploi du temps')
    last_fetched = Column(DateTime, nullable=True)
    owner = relationship('User', back_populates='icals')
    events = relationship('Event', back_populates='source')


class Event(Base):
    __tablename__ = 'events'
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey('ical_sources.id'), nullable=False)
    uid = Column(String(300), nullable=False)
    summary = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    location = Column(String(300), nullable=True)
    start = Column(DateTime, nullable=False)
    end = Column(DateTime, nullable=True)
    raw = Column(Text, nullable=True)
    __table_args__ = (UniqueConstraint('source_id', 'uid', name='_source_uid_uc'),)
    source = relationship('ICalSource', back_populates='events')


# Création des tables
Base.metadata.create_all(engine)


# Helpers
def db_session():
    return SessionLocal()


def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    db = db_session()
    user = db.query(User).filter(User.id == uid).first()
    db.close()
    return user


def fetch_all_icals_for_user(user_id):
    db = db_session()
    try:
        icals = db.query(ICalSource).filter(ICalSource.user_id == user_id).all()
        for src in icals:
            try:
                r = requests.get(src.url, timeout=FETCH_TIMEOUT)
                r.raise_for_status()
                cal = Calendar.from_ical(r.content)
                count = 0
                for component in cal.walk():
                    if component.name == 'VEVENT':
                        uid = str(component.get('uid'))
                        summary = str(component.get('summary') or '')
                        desc = str(component.get('description') or '')
                        loc = str(component.get('location') or '')
                        dtstart = component.get('dtstart').dt
                        dtend = component.get('dtend').dt if component.get('dtend') else None
                        if isinstance(dtstart, datetime) and dtstart.tzinfo is None:
                            dtstart = dtstart.replace(tzinfo=timezone.utc)
                        if dtend and isinstance(dtend, datetime) and dtend.tzinfo is None:
                            dtend = dtend.replace(tzinfo=timezone.utc)
                        existing = db.query(Event).filter(Event.source_id == src.id, Event.uid == uid).first()
                        if existing:
                            existing.summary = summary
                            existing.description = desc
                            existing.location = loc
                            existing.start = dtstart
                            existing.end = dtend
                            existing.raw = str(component)
                        else:
                            ev = Event(source_id=src.id, uid=uid, summary=summary, description=desc, location=loc, start=dtstart, end=dtend, raw=str(component))
                            db.add(ev)
                        count += 1
                src.last_fetched = datetime.now(timezone.utc)
                db.commit()
            except Exception as e:
                traceback.print_exc()
                print(f"Erreur lors de l'import de {src.url} : {str(e)}")
    finally:
        db.close()


def fetch_ical_for_source(source_id, db_session):
    src = db_session.query(ICalSource).filter(ICalSource.id == source_id).first()
    if not src:
        return False

    try:
        r = requests.get(src.url, timeout=FETCH_TIMEOUT)
        r.raise_for_status()
        cal = Calendar.from_ical(r.content)
        count = 0

        for component in cal.walk():
            if component.name == 'VEVENT':
                uid = str(component.get('uid'))
                summary = str(component.get('summary') or '')
                desc = str(component.get('description') or '')
                loc = str(component.get('location') or '')
                dtstart = component.get('dtstart').dt
                dtend = component.get('dtend').dt if component.get('dtend') else None

                if isinstance(dtstart, datetime) and dtstart.tzinfo is None:
                    dtstart = dtstart.replace(tzinfo=timezone.utc)
                if dtend and isinstance(dtend, datetime) and dtend.tzinfo is None:
                    dtend = dtend.replace(tzinfo=timezone.utc)

                existing = db_session.query(Event).filter(Event.source_id == src.id, Event.uid == uid).first()
                if existing:
                    existing.summary = summary
                    existing.description = desc
                    existing.location = loc
                    existing.start = dtstart
                    existing.end = dtend
                    existing.raw = str(component)
                else:
                    ev = Event(source_id=src.id, uid=uid, summary=summary, description=desc, location=loc, start=dtstart, end=dtend, raw=str(component))
                    db_session.add(ev)
                count += 1

        src.last_fetched = datetime.now(timezone.utc)
        db_session.commit()
        return True
    except Exception as e:
        traceback.print_exc()
        db_session.rollback()
        return False


# Routes
@app.route('/')
def index():
    return render_template('index.html', user=current_user())


from sqlalchemy.orm import joinedload


@app.route('/friends')
def friends():
    u = current_user()
    if not u:
        return redirect(url_for('login'))
    db = db_session()
    # Charge les amis acceptés
    accepted_friends = db.query(Friendship). \
        options(joinedload(Friendship.user), joinedload(Friendship.friend)). \
        filter(
        ((Friendship.user_id == u.id) | (Friendship.friend_id == u.id)),
        Friendship.status == 'accepted'
    ).all()
    # Charge les demandes en attente avec l'utilisateur qui les a envoyées
    pending_requests = db.query(Friendship). \
        options(joinedload(Friendship.user)). \
        filter(Friendship.friend_id == u.id, Friendship.status == 'pending').all()
    # Recherche d'utilisateurs
    search_email = request.args.get('search', '')
    users = []
    if search_email:
        users = db.query(User).filter(
            User.username.ilike(f'%{search_email}%'),
            User.id != u.id
        ).all()
    db.close()
    return render_template('friends.html',
                           user=u,
                           accepted_friends=accepted_friends,
                           pending_requests=pending_requests,
                           users=users)


@app.route('/send_friend_request/<int:friend_id>', methods=['POST'])
def send_friend_request(friend_id):
    u = current_user()
    if not u:
        return redirect(url_for('login'))

    db = db_session()
    friend = db.query(User).filter(User.id == friend_id).first()
    if not friend:
        flash('Utilisateur introuvable.', 'error')
        db.close()
        return redirect(url_for('friends'))

    existing_request = db.query(Friendship).filter(
        ((Friendship.user_id == u.id) & (Friendship.friend_id == friend_id)) |
        ((Friendship.user_id == friend_id) & (Friendship.friend_id == u.id))
    ).first()

    if existing_request:
        flash('Une demande existe déjà.', 'error')
        db.close()
        return redirect(url_for('friends'))

    friendship = Friendship(user_id=u.id, friend_id=friend_id, status='pending')
    db.add(friendship)
    db.commit()
    db.close()
    flash('Demande d\'ami envoyée !', 'success')
    return redirect(url_for('friends'))


@app.route('/accept_friend_request/<int:request_id>', methods=['POST'])
def accept_friend_request(request_id):
    u = current_user()
    if not u:
        return redirect(url_for('login'))

    db = db_session()
    request = db.query(Friendship).filter(Friendship.id == request_id, Friendship.friend_id == u.id).first()
    if not request:
        flash('Demande introuvable.', 'error')
        db.close()
        return redirect(url_for('friends'))

    request.status = 'accepted'
    db.commit()
    db.close()
    flash('Demande acceptée !', 'success')
    return redirect(url_for('friends'))


@app.route('/reject_friend_request/<int:request_id>', methods=['POST'])
def reject_friend_request(request_id):
    u = current_user()
    if not u:
        return redirect(url_for('login'))

    db = db_session()
    request = db.query(Friendship).filter(Friendship.id == request_id, Friendship.friend_id == u.id).first()
    if not request:
        flash('Demande introuvable.', 'error')
        db.close()
        return redirect(url_for('friends'))

    db.delete(request)
    db.commit()
    db.close()
    flash('Demande refusée.', 'success')
    return redirect(url_for('friends'))


@app.route('/remove_friend/<int:friendship_id>', methods=['POST'])
def remove_friend(friendship_id):
    u = current_user()
    if not u:
        return redirect(url_for('login'))

    db = db_session()
    friendship = db.query(Friendship).filter(Friendship.id == friendship_id).first()
    if not friendship or (friendship.user_id != u.id and friendship.friend_id != u.id):
        flash('Amitié introuvable.', 'error')
        db.close()
        return redirect(url_for('friends'))

    db.delete(friendship)
    db.commit()
    db.close()
    flash('Ami supprimé.', 'success')
    return redirect(url_for('friends'))


@app.route('/fetch_ical/<int:source_id>', methods=['POST'])
def fetch_ical(source_id):
    u = current_user()
    if not u:
        return redirect(url_for('login'))
    db = db_session()
    src = db.query(ICalSource).filter(ICalSource.id == source_id, ICalSource.user_id == u.id).first()
    if not src:
        db.close()
        flash('Source introuvable', 'error')
        return redirect(url_for('dashboard'))
    try:
        r = requests.get(src.url, timeout=FETCH_TIMEOUT)
        r.raise_for_status()
        cal = Calendar.from_ical(r.content)
        count = 0
        for component in cal.walk():
            if component.name == 'VEVENT':
                uid = str(component.get('uid'))
                summary = str(component.get('summary') or '')
                desc = str(component.get('description') or '')
                loc = str(component.get('location') or '')
                dtstart = component.get('dtstart').dt
                dtend = component.get('dtend').dt if component.get('dtend') else None
                if isinstance(dtstart, datetime) and dtstart.tzinfo is None:
                    dtstart = dtstart.replace(tzinfo=timezone.utc)
                if dtend and isinstance(dtend, datetime) and dtend.tzinfo is None:
                    dtend = dtend.replace(tzinfo=timezone.utc)
                existing = db.query(Event).filter(Event.source_id == src.id, Event.uid == uid).first()
                if existing:
                    existing.summary = summary
                    existing.description = desc
                    existing.location = loc
                    existing.start = dtstart
                    existing.end = dtend
                    existing.raw = str(component)
                else:
                    ev = Event(source_id=src.id, uid=uid, summary=summary, description=desc, location=loc, start=dtstart, end=dtend, raw=str(component))
                    db.add(ev)
                count += 1
        src.last_fetched = datetime.now(timezone.utc)
        db.commit()
        flash(f'Import terminé : {count} événements importés/mis à jour', 'success')
    except Exception as e:
        traceback.print_exc()
        flash(f'Erreur lors de l\'import iCal : {str(e)}', 'error')
    finally:
        db.close()
    return redirect(url_for('dashboard'))


@app.route('/delete_ical/<int:source_id>', methods=['POST'])
def delete_ical(source_id):
    u = current_user()
    if not u:
        return redirect(url_for('login'))
    db = db_session()
    src = db.query(ICalSource).filter(ICalSource.id == source_id, ICalSource.user_id == u.id).first()
    if not src:
        db.close()
        flash('Source introuvable', 'error')
        return redirect(url_for('dashboard'))
    db.query(Event).filter(Event.source_id == src.id).delete()
    db.delete(src)
    db.commit()
    db.close()
    flash('Source supprimée', 'success')
    return redirect(url_for('dashboard'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        pwd = request.form['password']
        db = db_session()
        if db.query(User).filter(User.username == username).first():
            flash('Nom d\'utilisateur déjà utilisé', 'error')
            db.close()
            return redirect(url_for('register'))
        u = User(username=username, password_hash=generate_password_hash(pwd))
        db.add(u)
        db.commit()
        db.close()
        flash('Compte créé avec succès ! Connecte-toi.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', user=current_user())


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip().lower()
        pwd = request.form['password']
        db = db_session()
        u = db.query(User).filter(User.username == username).first()
        if not u or not check_password_hash(u.password_hash, pwd):
            flash('Email ou mot de passe incorrect', 'error')
            db.close()
            return redirect(url_for('login'))
        session['user_id'] = u.id
        fetch_all_icals_for_user(u.id)
        db.close()
        return redirect(url_for('dashboard'))
    return render_template('login.html', user=current_user())


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('Tu es déconnecté.', 'info')
    return redirect(url_for('index'))


@app.route('/dashboard')
def dashboard():
    u = current_user()
    if not u:
        return redirect(url_for('login'))
    db = db_session()
    icals = db.query(ICalSource).filter(ICalSource.user_id == u.id).all()
    db.close()
    return render_template('dashboard.html', user=u, icals=icals)


@app.route('/add_ical', methods=['POST'])
def add_ical():
    u = current_user()
    if not u:
        return redirect(url_for('login'))

    url = request.form.get('url', '').strip()
    label = request.form.get('label', 'Mon emploi du temps').strip()

    if not url or not (url.startswith('http://') or url.startswith('https://')):
        flash('URL iCal invalide', 'error')
        return redirect(url_for('dashboard'))

    db = db_session()
    try:
        src = ICalSource(user_id=u.id, url=url, label=label)
        db.add(src)
        db.commit()
        fetch_ical_for_source(src.id, db)
        flash('Source iCal ajoutée et événements importés !', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Erreur lors de l\'ajout de la source iCal : {str(e)}', 'error')
    finally:
        db.close()

    return redirect(url_for('dashboard'))


@app.route('/agenda')
def agenda():
    u = current_user()
    if not u:
        return redirect(url_for('login'))

    db = db_session()
    srcs = db.query(ICalSource).filter(ICalSource.user_id == u.id).all()

    friendships = db.query(Friendship).filter(
        ((Friendship.user_id == u.id) | (Friendship.friend_id == u.id)) &
        (Friendship.status == 'accepted')
    ).all()

    friend_ids = []
    for friendship in friendships:
        if friendship.user_id == u.id:
            friend_ids.append(friendship.friend_id)
        else:
            friend_ids.append(friendship.user_id)

    if friend_ids:
        friend_srcs = db.query(ICalSource).filter(ICalSource.user_id.in_(friend_ids)).all()
    else:
        friend_srcs = []

    events = []
    for s in srcs:
        for ev in s.events:
            if ev.start and ev.end:
                events.append({
                    'title': f"{ev.summary} (Moi)",
                    'start': ev.start.isoformat(),
                    'end': ev.end.isoformat(),
                    'backgroundColor': f'#{hash(s.label) % 0xFFFFFF:06x}',
                    'borderColor': f'#{hash(s.label) % 0xFFFFFF:06x}',
                })

    db.close()
    return render_template('agenda.html', user=u, events=json.dumps(events))


@app.route('/friend_agenda/<int:friend_id>')
def friend_agenda(friend_id):
    u = current_user()
    if not u:
        return redirect(url_for('login'))

    db = db_session()

    friendship = db.query(Friendship).filter(
        ((Friendship.user_id == u.id) & (Friendship.friend_id == friend_id)) |
        ((Friendship.friend_id == u.id) & (Friendship.user_id == friend_id)),
        Friendship.status == 'accepted'
    ).first()

    if not friendship:
        flash("Cet utilisateur n'est pas dans ta liste d'amis.", 'error')
        return redirect(url_for('agenda'))

    srcs = db.query(ICalSource).filter(ICalSource.user_id == friend_id).all()
    events = []
    for s in srcs:
        for ev in s.events:
            if ev.start and ev.end:
                events.append({
                    'title': f"{ev.summary} (Ami)",
                    'start': ev.start.isoformat(),
                    'end': ev.end.isoformat(),
                    'backgroundColor': f'#{(hash(s.label) + 1000) % 0xFFFFFF:06x}',
                    'borderColor': f'#{(hash(s.label) + 1000) % 0xFFFFFF:06x}',
                })

    friend = db.query(User).filter(User.id == friend_id).first()

    db.close()
    return render_template('friend_agenda.html', user=u, friend=friend, events=json.dumps(events))


if __name__ == '__main__':
    app.run(debug=True)
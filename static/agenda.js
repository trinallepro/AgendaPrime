document.addEventListener('DOMContentLoaded', function() {
    const calendarEl = document.getElementById('calendar');
    const eventsData = document.getElementById('events-data');

    // Vérifie que eventsData existe et contient des données
    if (!eventsData) {
        console.error("Éléments events-data introuvable !");
        return;
    }

    let events = [];
    try {
        events = JSON.parse(eventsData.textContent);
        console.log("Événements chargés :", events); // Affiche les événements dans la console
    } catch (e) {
        console.error("Erreur lors de l'analyse des événements :", e);
    }

    const calendar = new FullCalendar.Calendar(calendarEl, {
        initialView: 'timeGridWeek',
        headerToolbar: {
            left: 'prev,next today',
            center: 'title',
            right: 'dayGridMonth,timeGridWeek,timeGridDay,listWeek'
        },
        events: events,
        eventDisplay: 'block',
        eventTimeFormat: {
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        },
        locale: 'fr',
        height: 'auto',
        allDaySlot: false,
        slotMinTime: '07:00:00',
        slotMaxTime: '20:00:00',
    });

    calendar.render();
});

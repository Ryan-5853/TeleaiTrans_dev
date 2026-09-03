import { createRouter, createWebHistory } from "vue-router";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "fleet", component: () => import("./views/FleetView.vue") },
    {
      path: "/aircraft/:id",
      name: "aircraft",
      component: () => import("./views/AircraftDetailView.vue")
    },
    {
      path: "/firmware",
      name: "firmware",
      component: () => import("./views/FirmwareView.vue")
    },
    {
      path: "/events",
      name: "events",
      component: () => import("./views/EventsView.vue")
    }
  ]
});

export default router;

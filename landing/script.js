// Navigation Toggle
const navToggle = document.querySelector(".nav-toggle");
const nav = document.querySelector(".nav");
const header = document.querySelector(".site-header");

const toggleNav = () => {
  const isOpen = nav.classList.toggle("open");
  navToggle.setAttribute("aria-expanded", String(isOpen));
  document.body.style.overflow = isOpen ? "hidden" : "";
};

if (navToggle) {
  navToggle.addEventListener("click", toggleNav);
}

// Smooth scroll for internal links
document.querySelectorAll('a[href^="#"]').forEach((link) => {
  link.addEventListener("click", (e) => {
    const targetId = link.getAttribute("href")?.slice(1);
    const target = targetId ? document.getElementById(targetId) : null;
    if (target) {
      e.preventDefault();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
      if (nav.classList.contains("open")) {
        toggleNav();
      }
    }
  });
});

// Close nav on outside click (mobile)
document.addEventListener("click", (e) => {
  const clickedInsideNav = nav.contains(e.target);
  const clickedToggle = navToggle.contains(e.target);
  if (!clickedInsideNav && !clickedToggle && nav.classList.contains("open")) {
    toggleNav();
  }
});

// Header scroll effect
let lastScroll = 0;
const scrollThreshold = 100;

window.addEventListener("scroll", () => {
  const currentScroll = window.pageYOffset;
  
  if (currentScroll > scrollThreshold) {
    header.style.background = "rgba(15, 23, 41, 0.95)";
    header.style.boxShadow = "0 4px 20px rgba(0, 0, 0, 0.3)";
  } else {
    header.style.background = "rgba(15, 23, 41, 0.85)";
    header.style.boxShadow = "none";
  }
  
  lastScroll = currentScroll;
});

// Intersection Observer for animations
const observerOptions = {
  threshold: 0.1,
  rootMargin: "0px 0px -100px 0px"
};

const observer = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.style.opacity = "1";
      entry.target.style.transform = "translateY(0)";
    }
  });
}, observerOptions);

// Animate elements on scroll
document.querySelectorAll('.feature-card, .metric-card, .testimonial, .pricing-card, .about-flow-card').forEach(el => {
  el.style.opacity = "0";
  el.style.transform = "translateY(30px)";
  el.style.transition = "opacity 0.6s ease-out, transform 0.6s ease-out";
  observer.observe(el);
});

// Add ripple effect to buttons
document.querySelectorAll('.btn').forEach(button => {
  button.addEventListener('click', function(e) {
    const ripple = document.createElement('span');
    const rect = this.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    const x = e.clientX - rect.left - size / 2;
    const y = e.clientY - rect.top - size / 2;
    
    ripple.style.width = ripple.style.height = size + 'px';
    ripple.style.left = x + 'px';
    ripple.style.top = y + 'px';
    ripple.classList.add('ripple');
    
    this.appendChild(ripple);
    
    setTimeout(() => ripple.remove(), 600);
  });
});

// Lazy loading for heavy content
if ('IntersectionObserver' in window) {
  const lazyLoadObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('loaded');
        lazyLoadObserver.unobserve(entry.target);
      }
    });
  });

  document.querySelectorAll('.lazy-load').forEach(el => {
    lazyLoadObserver.observe(el);
  });
}

// Performance optimization: Debounce resize events
let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    // Recalculate layouts if needed
    if (window.innerWidth > 800 && nav.classList.contains('open')) {
      toggleNav();
    }
  }, 250);
});

// Keyboard navigation improvements
document.addEventListener('keydown', (e) => {
  // Close mobile menu with Escape key
  if (e.key === 'Escape' && nav.classList.contains('open')) {
    toggleNav();
  }
});

// Add focus trap for mobile menu
const focusableElements = nav.querySelectorAll('a, button');
const firstFocusable = focusableElements[0];
const lastFocusable = focusableElements[focusableElements.length - 1];

nav.addEventListener('keydown', (e) => {
  if (e.key === 'Tab' && nav.classList.contains('open')) {
    if (e.shiftKey && document.activeElement === firstFocusable) {
      e.preventDefault();
      lastFocusable.focus();
    } else if (!e.shiftKey && document.activeElement === lastFocusable) {
      e.preventDefault();
      firstFocusable.focus();
    }
  }
});

console.log('🚀 NIQ Desktop - Landing page loaded successfully');


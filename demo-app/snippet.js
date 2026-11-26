// ExpAgent-generated snippet — flag: checkout-cta, metric event: checkout_completed
// Produced by: python3 agent/plan.py "A/B test checkout headline, 50/50, measure checkouts, 2 weeks"
// (flag/value pairs below use the pre-work Cloud flag: control "Buy now",
//  variant_b "Start free trial". Paste your Development environment key in.)
import flagsmith from 'flagsmith';

await flagsmith.init({
  environmentID: '<YOUR_ENVIRONMENT_ID>', // Development env key
  api: 'https://edge.api.flagsmith.com/api/v1/',
});

// Read the flag value (variant copy) for this identity:
const value = flagsmith.getValue('checkout-cta');
document.getElementById('cta').textContent = value;

// Record the conversion event when it happens:
flagsmith.track('checkout_completed');

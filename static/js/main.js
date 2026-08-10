console.log(
"Prestigious Real Estate Website Loaded"
);



const track = document.querySelector(".property-track");

const next = document.querySelector(".next");

const prev = document.querySelector(".prev");


let position = 0;



if(track){


next.addEventListener("click",()=>{


position -= 380;


if(position < -760){

position = 0;

}


track.style.transform =
`translateX(${position}px)`;


});





prev.addEventListener("click",()=>{


position += 380;


if(position > 0){

position = -760;

}


track.style.transform =
`translateX(${position}px)`;


});





// automatic movement


setInterval(()=>{


next.click();


},4000);



}


// ====================================
// HERO IMAGE CAROUSEL
// ====================================

const heroSlides = document.querySelectorAll(".hero-slide");

if (heroSlides.length > 0) {

    let currentHero = 0;

    function showHeroSlide(index) {

        heroSlides.forEach(slide => {
            slide.classList.remove("active");
        });

        heroSlides[index].classList.add("active");
    }

    setInterval(() => {

        currentHero++;

        if (currentHero >= heroSlides.length) {
            currentHero = 0;
        }

        showHeroSlide(currentHero);

    }, 5000);

}


// ================================
// MOBILE MENU
// ================================


const mobileMenuBtn = document.getElementById("mobileMenuBtn");

const navMenu = document.getElementById("navMenu");


if(mobileMenuBtn && navMenu){


mobileMenuBtn.addEventListener("click",()=>{


navMenu.classList.toggle("active");


});


}
